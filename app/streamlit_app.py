"""停车场管理智能体 —— Streamlit 前端（开发方案 §6.6）。

三块布局：
  左：聊天窗（对话 + 引用片段折叠展示）
  右上：抓拍演示区（选相机/变体 → 双通道识别 → 结果徽章 + 抓拍图）
  右下：车位看板（A/B/C 区余位进度条，读 mock /lots/status）

启动（项目根目录）：
    uvicorn mock_service.main:app --port 8000     # 终端1
    streamlit run app/streamlit_app.py            # 终端2 → http://localhost:8501
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import streamlit as st  # noqa: E402

from agent.graph import build_agent, chat_with_trace  # noqa: E402
from agent.vision import recognize_plate  # noqa: E402
from common.config import MOCK_BASE  # noqa: E402

st.set_page_config(page_title="停车场管理智能体", page_icon="🅿️", layout="wide")

# 徽章：source → (显示文案, 颜色)
BADGES = {
    "both_agree": ("双通道一致", "green"),
    "both_conflict": ("存在分歧（以OCR为准）", "orange"),
    "ocr_only": ("仅本地OCR", "gray"),
    "vlm_only": ("VLM兜底", "blue"),
    "failed": ("识别失败，请重拍", "red"),
}
CAMERAS = {"cam-1": "A 区入口", "cam-2": "B 区入口", "cam-3": "C 区入口", "cam-4": "出口"}


@st.cache_resource(show_spinner="正在加载智能体…")
def get_agent():
    return build_agent()


def mock_up() -> bool:
    try:
        return httpx.get(f"{MOCK_BASE}/lots/status", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


if "thread_id" not in st.session_state:
    st.session_state.thread_id = f"ui-{uuid.uuid4().hex[:8]}"
if "history" not in st.session_state:
    st.session_state.history = []          # [{role, text, snippets}]
if "capture" not in st.session_state:
    st.session_state.capture = None        # 最近一次识别结果

st.title("🅿️ 停车场管理智能体")
st.caption(f"会话 {st.session_state.thread_id}（多轮记忆落盘，重启前端不丢）")

if not mock_up():
    st.error(f"Mock 停车场服务未启动（{MOCK_BASE}）。请先运行："
             "`uvicorn mock_service.main:app --port 8000`")
    st.stop()

left, right = st.columns([3, 2], gap="large")

# ────────────────────────── 左：聊天窗 ──────────────────────────
with left:
    st.subheader("对话")
    chat_box = st.container(height=560)
    with chat_box:
        for item in st.session_state.history:
            with st.chat_message(item["role"]):
                st.markdown(item["text"])
                if item.get("snippets"):
                    with st.expander(f"引用片段（{len(item['snippets'])} 条）"):
                        for sn in item["snippets"]:
                            st.markdown(f"**[{sn['chunk_id']}]** {sn['source']}")
                            st.caption(sn["text"])

    prompt = st.chat_input("例如：A区还有多少车位？/ 让 cam-1 的车入场 / 小型车怎么收费？")
    if prompt:
        st.session_state.history.append({"role": "user", "text": prompt})
        with chat_box:
            with st.chat_message("user"):
                st.markdown(prompt)
            with st.chat_message("assistant"):
                with st.spinner("智能体思考中…"):
                    try:
                        reply, snippets = chat_with_trace(
                            get_agent(), st.session_state.thread_id, prompt)
                    except Exception as e:  # noqa: BLE001
                        msg = str(e)
                        if "429" in msg or "frequent" in msg.lower():
                            reply, snippets = "（请求过于频繁，请稍等几秒后重发一次）", []
                        else:
                            reply, snippets = f"（出错，请重试）{msg[:200]}", []
                st.markdown(reply)
                if snippets:
                    with st.expander(f"引用片段（{len(snippets)} 条）"):
                        for sn in snippets:
                            st.markdown(f"**[{sn['chunk_id']}]** {sn['source']}")
                            st.caption(sn["text"])
        st.session_state.history.append(
            {"role": "assistant", "text": reply, "snippets": snippets})

# ────────────────────────── 右：抓拍 + 看板 ──────────────────────────
with right:
    st.subheader("抓拍识别演示")
    c1, c2 = st.columns(2)
    cam = c1.selectbox("相机", list(CAMERAS), format_func=lambda k: f"{k}（{CAMERAS[k]}）")
    variant = c2.selectbox("变体", ["normal", "blur", "occluded"],
                           format_func=lambda v: {"normal": "正常", "blur": "模糊",
                                                  "occluded": "遮挡"}[v])
    if st.button("📷 抓拍识别", use_container_width=True, type="primary"):
        with st.spinner("双通道识别中（本地OCR ∥ 视觉模型）…"):
            st.session_state.capture = recognize_plate(cam, variant)

    cap = st.session_state.capture
    if cap:
        text, color = BADGES.get(cap["source"], (cap["source"], "gray"))
        st.markdown(f"**结果徽章：** :{color}[{text}]")
        if cap.get("plate"):
            st.markdown(f"### {cap['plate']}")
        ocr_plate = (cap["ocr"] or {}).get("plate") or "未识别"
        vlm_plate = (cap["vlm"] or {}).get("plate") or "未识别"
        elapsed = f"{cap['elapsed_ms']} ms" if cap["elapsed_ms"] is not None else "-"
        st.markdown(f"本地 OCR：**{ocr_plate}**　｜　视觉模型：**{vlm_plate}**　｜　耗时 **{elapsed}**")
        if cap.get("error"):
            st.warning(cap["error"])
        if cap.get("image_path") and Path(cap["image_path"]).exists():
            st.image(cap["image_path"], caption=f"{cam} · {variant}", use_container_width=True)

    st.divider()
    st.subheader("车位看板")
    if st.button("🔄 刷新", use_container_width=True) or True:
        try:
            data = httpx.get(f"{MOCK_BASE}/lots/status", timeout=5).json()
            for a in data["areas"]:
                ratio = a["free"] / a["total"] if a["total"] else 0
                st.markdown(f"**{a['area']} 区 · {a['name']}**")
                st.progress(ratio, text=f"空闲 {a['free']}/{a['total']}")
            st.caption(f"更新时间：{data['updated_at']}")
        except Exception as e:  # noqa: BLE001
            st.warning(f"看板加载失败：{e}")

    with st.expander("调试工具（演示用）"):
        d1, d2 = st.columns(2)
        if d1.button("时钟 +2 小时", use_container_width=True):
            httpx.post(f"{MOCK_BASE}/debug/time", json={"offset_minutes": 120}, timeout=5)
            st.toast("虚拟时间已 +120 分钟")
        if d2.button("全部复位", use_container_width=True):
            httpx.post(f"{MOCK_BASE}/debug/reset", timeout=5)
            st.session_state.capture = None
            st.toast("车位/票据/时钟已复位")
