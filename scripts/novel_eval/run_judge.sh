#!/usr/bin/env bash
# 批量评测：对 EVAL_BASE 下每个小说目录的 {novel}-result-*.json 调用 claude -p
# 使用 judge_prompt.md 作为模板，按 MAX_JOBS 并发执行。
#
# 用法：
#   ./run_judge.sh                          # 默认仅评测 诛仙-result-qwen.json
#   ./run_judge.sh '*'                      # 全量（所有 novel × 所有 system）
#   ./run_judge.sh '诛仙-result-*'           # 诛仙的所有 system
#   ./run_judge.sh '*-result-qwen' '*-result-ov-lite'   # 多个通配，任一命中即跑
#   ./run_judge.sh '*'                      # 拍平实验全量（EVAL_BASE 默认已指向 02-OpenViking 文件系统拍平 RAG 评测）
#
# 可调环境变量：
#   MAX_JOBS    并发数，默认 3
#   EVAL_BASE   评测根目录，默认指向「02-OpenViking 文件系统拍平 RAG 评测」
#   TEMPLATE    judge prompt 模板，默认同目录下 judge_prompt.md
#   LOG_DIR       日志目录，默认 EVAL_BASE/logs
#   CLAUDE_BIN    claude 可执行文件名，默认 claude
#   CLAUDE_MODEL  传给 claude 的 --model，默认 claude-opus-4-7（Opus 4.7）；
#                 上一次跑用的是默认 sonnet-4-6，准确性优先所以换 Opus
#   DRY_RUN       真值（1/true/yes/on）时只渲染 prompt 并写日志，不真正调用 claude；
#                 0/false/no/off/空 视为关闭

set -euo pipefail

# 兼容 bash 3.2（macOS 自带）：
# - 所有变量展开使用 ${var} 显式花括号，避免 bash 3.2 在 UTF-8 locale 下把
#   $var 后面的中文字节误认作变量名的一部分；
# - 不使用关联数组 / wait -n；
# - 数组在 set -u 下展开统一用 ${arr[@]+"${arr[@]}"} 形式。

MAX_JOBS="${MAX_JOBS:-3}"
EVAL_BASE="${EVAL_BASE:-/Users/bytedance/Documents/byterec/viking_evals/奇境 AI 小说评测实验/02-OpenViking 文件系统拍平 RAG 评测}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="${TEMPLATE:-$SCRIPT_DIR/judge_prompt.md}"
LOG_DIR="${LOG_DIR:-$EVAL_BASE/logs}"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"
CLAUDE_MODEL="${CLAUDE_MODEL:-claude-opus-4-7}"
# 把 DRY_RUN 规范化成 "" 或 "1"
case "$(printf '%s' "${DRY_RUN:-}" | tr '[:upper:]' '[:lower:]')" in
  ""|0|false|no|off) DRY_RUN="" ;;
  *) DRY_RUN=1 ;;
esac

[[ -f "$TEMPLATE" ]] || { echo "找不到模板：$TEMPLATE" >&2; exit 1; }
[[ -d "$EVAL_BASE" ]] || { echo "找不到评测目录：$EVAL_BASE" >&2; exit 1; }
if [[ -z "${DRY_RUN}" ]]; then
  command -v "$CLAUDE_BIN" >/dev/null || { echo "找不到 claude 命令：$CLAUDE_BIN" >&2; exit 1; }
fi

mkdir -p "$LOG_DIR"

# 默认仅评测「诛仙-result-qwen」，可通过位置参数覆盖
if (( $# == 0 )); then
  PATTERNS=('诛仙-result-qwen')
else
  PATTERNS=("$@")
fi

# 通配匹配：基名（不含 .json）命中任一 pattern 即视为目标
match_any() {
  local stem="$1" pat
  for pat in "${PATTERNS[@]}"; do
    # shellcheck disable=SC2053
    [[ "$stem" == $pat ]] && return 0
  done
  return 1
}

# 用 python 做模板占位替换，规避 bash/sed 对中文与特殊字符的踩坑
render_prompt() {
  python3 - "$TEMPLATE" "$1" "$2" "$3" <<'PY'
import sys
tpl, name, novel_path, judge_path = sys.argv[1:5]
with open(tpl, encoding='utf-8') as f:
    s = f.read()
s = s.replace('{{NOVEL_NAME}}', name)
s = s.replace('{{NOVEL_PATH}}', novel_path)
s = s.replace('{{JUDGE_JSON_PATH}}', judge_path)
sys.stdout.write(s)
PY
}

# 收集 (novel_name, novel_md, judge_json) 任务列表
JOBS=()
for novel_dir in "$EVAL_BASE"/*/; do
  novel="$(basename "${novel_dir}")"
  novel_md="${novel_dir}${novel}.md"
  if [[ ! -f "${novel_md}" ]]; then
    echo "跳过 ${novel}: 缺少 ${novel}.md"
    continue
  fi
  shopt -s nullglob
  for judge_json in "${novel_dir}${novel}"-result-*.json; do
    stem="$(basename "${judge_json}" .json)"
    if match_any "${stem}"; then
      JOBS+=("${novel}"$'\t'"${novel_md}"$'\t'"${judge_json}")
    fi
  done
  shopt -u nullglob
done

if (( ${#JOBS[@]} == 0 )); then
  echo "没有命中任何评测任务（PATTERNS=${PATTERNS[*]}）" >&2
  exit 2
fi

echo "本次将评测 ${#JOBS[@]} 个任务，并发 ${MAX_JOBS}，日志目录：${LOG_DIR}"
for job in "${JOBS[@]}"; do
  IFS=$'\t' read -r novel _ judge_json <<<"${job}"
  echo "  - [${novel}] $(basename "${judge_json}")"
done
echo

run_one() {
  local novel="$1" novel_md="$2" judge_json="$3"
  local stem; stem="$(basename "$judge_json" .json)"
  local ts; ts="$(date +%Y%m%d-%H%M%S)"
  local log="$LOG_DIR/${stem}.${ts}.log"
  local prompt
  prompt="$(render_prompt "$novel" "$novel_md" "$judge_json")"

  {
    echo "===== judge start ====="
    echo "novel:       ${novel}"
    echo "novel_md:    ${novel_md}"
    echo "judge_json:  ${judge_json}"
    echo "started_at:  $(date -Iseconds)"
    echo "dry_run:     ${DRY_RUN:-0}"
    if [[ -z "${DRY_RUN}" ]]; then
      echo "claude:      $(command -v "${CLAUDE_BIN}")"
      echo "model:       ${CLAUDE_MODEL}"
    fi
    echo "----- rendered prompt (full) -----"
    printf '%s\n' "${prompt}"
    echo "----- end of prompt -----"
  } >>"${log}"

  local rc=0
  if [[ -n "${DRY_RUN}" ]]; then
    echo "[dry-run] ${stem}  →  ${log}"
  else
    {
      echo "----- claude output -----"
    } >>"${log}"
    echo "[${stem}] ===== start (log: ${log}) ====="
    # --output-format stream-json + --verbose:
    #   claude 默认 text 输出是「一次性出最终结果」，运行中啥都不打——
    #   想看到工具调用/思考流，必须用 stream-json，它每完成一步就吐一行 JSON。
    # --setting-sources=    禁用所有 settings 加载（含 hooks/plugins 注入），
    #                       让 claude-mem 这类记忆 plugin 的 SessionStart hook 不再触发。
    # --strict-mcp-config   不自动加载用户级 MCP，让 claude-mem 的 MCP server 不会被拉起。
    # 综合效果：本次 judge 任务是一次干净、无记忆、无 plugin、可观测的孤立运行。
    # 用 while read 做行级转发：每行同时写日志、打到终端（带 [stem] 前缀），
    # 既能 tail -f 看日志，也能在 stdout 实时看到，且并发时多个任务的输出不会混乱。
    "${CLAUDE_BIN}" -p "${prompt}" \
      --model "${CLAUDE_MODEL}" \
      --dangerously-skip-permissions \
      --setting-sources= \
      --strict-mcp-config \
      --output-format stream-json \
      --verbose 2>&1 \
      | while IFS= read -r line; do
          printf '%s\n' "${line}" >>"${log}"
          printf '[%s] %s\n' "${stem}" "${line}"
        done \
      || rc=$?
    {
      echo "----- end -----"
      echo "exit_code: ${rc}"
      echo "ended_at:  $(date -Iseconds)"
    } >>"${log}"
    if (( rc == 0 )); then
      echo "[${stem}] ===== done ====="
      echo "[done] ${stem}  →  ${log}"
    else
      echo "[${stem}] ===== FAIL rc=${rc} =====" >&2
      echo "[FAIL rc=${rc}] ${stem}  →  ${log}" >&2
    fi
  fi
  return "${rc}"
}

# 简单的并发槽位控制（bash 4+）
PIDS=()
FAIL_COUNT=0

reap_one() {
  # 阻塞等待至少一个 PID 退出，然后从 PIDS 中移除已退出者
  local pid
  while :; do
    local alive=()
    for pid in "${PIDS[@]}"; do
      if kill -0 "${pid}" 2>/dev/null; then
        alive+=("${pid}")
      else
        wait "${pid}" || FAIL_COUNT=$((FAIL_COUNT + 1))
      fi
    done
    if (( ${#alive[@]} < ${#PIDS[@]} )); then
      PIDS=("${alive[@]+"${alive[@]}"}")
      return 0
    fi
    sleep 1
  done
}

for job in "${JOBS[@]}"; do
  IFS=$'\t' read -r novel novel_md judge_json <<<"${job}"
  while (( ${#PIDS[@]} >= MAX_JOBS )); do
    reap_one
  done
  run_one "${novel}" "${novel_md}" "${judge_json}" &
  PIDS+=("$!")
done

# 等待剩余任务
while (( ${#PIDS[@]} > 0 )); do
  reap_one
done

echo
if (( FAIL_COUNT == 0 )); then
  echo "全部 ${#JOBS[@]} 个任务完成。"
else
  echo "完成 ${#JOBS[@]} 个任务，其中 ${FAIL_COUNT} 个失败。详见 ${LOG_DIR}" >&2
  exit 1
fi
