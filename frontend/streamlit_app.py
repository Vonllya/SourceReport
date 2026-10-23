from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sourcereport.app import create_runtime  # noqa: E402
from sourcereport.config import Settings  # noqa: E402

SOURCE_STATUS_LABELS = {
    "fetched": "已读取原文",
    "snippet_fallback": "使用搜索摘要",
    "search_result": "仅有搜索结果",
}
NODE_LABELS = {
    "plan_research": "规划研究问题",
    "search_web": "搜索资料",
    "fetch_sources": "读取来源",
    "extract_evidence": "提取证据",
    "evaluate_evidence": "评估证据",
    "refine_queries": "补充搜索查询",
    "write_report": "生成报告",
    "verify_citations": "核验引用与修订报告",
}


def _fetch_failure_summary(message: str) -> tuple[str, str]:
    prefix, _, detail = message.partition(": ")
    if not prefix.startswith("fetch failed for "):
        return "工作流错误", message
    url = prefix.removeprefix("fetch failed for ")
    if "403 Forbidden" in detail:
        reason = "目标站点拒绝自动读取（HTTP 403）；如有搜索摘要，将保留摘要供研究使用。"
    elif "405 Not Allowed" in detail:
        reason = "目标站点拒绝此读取请求（HTTP 405）；如有搜索摘要，将保留摘要供研究使用。"
    elif "parsed application/pdf body was empty" in detail:
        reason = "PDF 已下载但未提取出文本（可能是扫描件或加密文件）；如有搜索摘要，将保留摘要供研究使用。"
    elif "unsafe or unresolvable URL" in detail:
        reason = "无法确认该链接是可访问的公网地址，已跳过读取。"
    else:
        reason = "未能读取网页原文；如有搜索摘要，将保留摘要供研究使用。"
    return url, reason


load_dotenv()
st.set_page_config(page_title="SourceReport", page_icon="🔎", layout="wide")
st.title("🔎 SourceReport")
st.caption("输入研究问题，获取带来源引用的报告，查看证据与引用核验结果。")

if "runtime" not in st.session_state:
    try:
        st.session_state.runtime = create_runtime(Settings.from_env())
    except ValueError as exc:
        st.error("模型或搜索服务配置不完整，请检查项目根目录的 .env 文件。")
        st.info("填写 OPENAI_API_KEY 和 TAVILY_API_KEY，并确认服务、接口地址与模型名配置正确，然后重启应用。")
        with st.expander("配置错误详情"):
            st.text(str(exc))
        st.stop()
runtime = st.session_state.runtime

with st.sidebar:
    st.subheader("运行状态")
    st.write(f"模型：{runtime.llm_mode}")
    st.write(f"搜索：{runtime.search_mode}")
    if not runtime.runnable:
        st.warning("模型服务尚未就绪。请检查 .env 中的模型配置和 API Key，然后重启应用。")
    st.subheader("历史任务")
    history = runtime.workflow.store.list_tasks() if runtime.workflow.store else []
    labels = {f"{x['question'][:35]} · {x['created_at']}": x["task_id"] for x in history}
    selected = st.selectbox("选择历史报告", [""] + list(labels))
    if selected:
        selected_task_id = labels[selected]
        old = runtime.workflow.store.load_state(selected_task_id)
        if old:
            st.session_state.result = old
        st.caption("删除操作会移除该任务及其来源、节点记录和错误记录。")
        if st.button("删除所选报告", type="secondary"):
            runtime.workflow.store.delete_task(selected_task_id)
            if st.session_state.get("result", {}).get("task_id") == selected_task_id:
                st.session_state.pop("result", None)
            st.rerun()

question = st.text_area("研究问题", placeholder="例如：如何设计一个可靠、可验证的深度研究 Agent？", height=100)
max_rounds = st.radio("最大研究轮数", options=[1, 2, 3], index=1, horizontal=True)
source_preference = st.selectbox(
    "来源偏好",
    options=["不限定来源类型", "官方文档", "GitHub", "论文平台", "博客", "媒体"],
    help="该偏好会写入研究计划，指导搜索查询优先选择相应来源。最终来源仍由实际搜索结果和抓取状态决定。",
)
max_sources = st.slider(
    "最多抓取来源数",
    min_value=10,
    max_value=30,
    value=10,
    step=1,
    help="控制本次研究最多保留和抓取的去重来源数量。",
)
start = st.button("开始研究", type="primary", disabled=not runtime.runnable or not question.strip())

if start:
    status = st.status("研究进行中…", expanded=True)
    current = st.empty()
    state = runtime.workflow.initial_state(
        question, max_rounds, source_preference=source_preference, max_sources=max_sources
    )

    async def stream_run():
        latest = state
        async for event in runtime.workflow.graph.astream(state, {"recursion_limit": 30}, stream_mode="updates"):
            for node, update in event.items():
                label = NODE_LABELS.get(node, node)
                current.info(f"已完成：{label}")
                status.write(f"✓ {label}")
                latest.update(update)
        return latest

    try:
        st.session_state.result = asyncio.run(stream_run())
        status.update(label="研究完成", state="complete", expanded=False)
    except Exception as exc:
        status.update(label="研究失败", state="error")
        st.error(f"执行失败：{type(exc).__name__}: {exc}")

result = st.session_state.get("result")
if result:
    tab_report, tab_process, tab_audit = st.tabs(["研究报告", "过程与来源", "引用核验"])
    with tab_report:
        st.markdown(result.get("report") or "尚无报告")
    with tab_process:
        st.subheader("研究计划")
        st.json(result.get("research_plan", {}))
        st.subheader("已执行查询")
        st.write(result.get("completed_queries", []))
        st.subheader("来源")
        source_counts = Counter(source.get("source_status", "search_result") for source in result.get("sources", []))
        status_cols = st.columns(3)
        status_cols[0].metric("已读取原文", source_counts.get("fetched", 0))
        status_cols[1].metric("使用搜索摘要", source_counts.get("snippet_fallback", 0))
        status_cols[2].metric("仅搜索结果", source_counts.get("search_result", 0))
        for source in result.get("sources", []):
            status = source.get("source_status", "search_result")
            label = SOURCE_STATUS_LABELS.get(status, status)
            st.markdown(f"**[{source['source_id']}] [{source['title']}]({source['url']})** · {label}")
        if result.get("errors"):
            with st.expander(f"抓取问题（{len(result['errors'])}）", expanded=False):
                for error in result["errors"]:
                    target, reason = _fetch_failure_summary(error)
                    st.markdown(f"- `{target}`：{reason}")
    with tab_audit:
        audit = result.get("citation_audit", {})
        st.metric("引用覆盖率", f"{audit.get('coverage', 0):.0%}")
        st.dataframe(audit.get("checks", []), use_container_width=True)
        metrics = result.get("metrics", {})
        cols = st.columns(5)
        cols[0].metric("Token", metrics.get("total_tokens", 0))
        cols[1].metric("耗时", f"{metrics.get('elapsed_seconds', 0):.2f}s")
        cols[2].metric("搜索次数", metrics.get("search_count", 0))
        cols[3].metric("抓取次数", metrics.get("fetch_count", 0))
        cols[4].metric("模型调用", metrics.get("model_calls", 0))
        with st.expander("模型与节点指标"):
            st.json({
                "model": metrics.get("model"),
                "prompt_tokens": metrics.get("prompt_tokens", 0),
                "completion_tokens": metrics.get("completion_tokens", 0),
                "reasoning_tokens": metrics.get("reasoning_tokens", 0),
                "cached_tokens": metrics.get("cached_tokens", 0),
                "retry_count": metrics.get("retry_count", 0),
                "fetch_attempt_count": metrics.get("fetch_attempt_count", 0),
                "fetch_retry_count": metrics.get("fetch_retry_count", 0),
                "timeout_retry_count": metrics.get("timeout_retry_count", 0),
                "node_metrics": metrics.get("node_metrics", {}),
                "calls": metrics.get("call_metrics", []),
            })
