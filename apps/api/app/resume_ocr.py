"""扫描件/图片型 PDF 的 OCR 兜底（vision 分层多模态模型）。

链路：pypdfium2 内嵌的 PDFium 引擎把 PDF 逐页渲染成 PNG（无外部二进制依赖，
区别于需装 poppler 的 pdf2image）→ 按 OpenAI 视觉消息格式发给 vision 分层
逐页转写为纯文本 → clean 后交给 analyzer 走正常结构化分析。

页数与清晰度在常量处收紧（简历 1~3 页、≈144 DPI），控制 token 与耗时成本。
"""

import asyncio
import base64
import io

from app import llm, resume_parser

MAX_PAGES = 3  # 简历通常 1~3 页，超出按前 3 页转写
RENDER_SCALE = 2.0  # 72 DPI × 2 ≈ 144 DPI，中文小字号可辨识且图片体积可控
LLM_TIMEOUT_SEC = 120  # vision 分层默认 60s 偏紧，多页图一次调用放宽

_OCR_PROMPT = (
    "以下是一份简历扫描件按顺序排列的逐页截图。"
    "请把每页中所有可读文字完整转写为纯文本：保持原有段落与条目顺序，"
    "不要总结、改写或补充不存在的内容；各页之间用「=== 第 N 页 ===」分隔；"
    "照片、签名、装饰图形等无关内容可忽略。只输出转写结果。"
)


class OcrError(RuntimeError):
    """OCR 结果不可用（页图渲染失败 / 模型转写无有效内容）。"""


def render_pages(data: bytes) -> list[bytes]:
    """把 PDF 前几页渲染为 PNG 字节列表；损坏文件与空页抛 OcrError。"""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(io.BytesIO(data))
        if len(pdf) == 0:
            raise OcrError("PDF 无有效页面")
        pages: list[bytes] = []
        for index in range(min(len(pdf), MAX_PAGES)):
            pil_image = pdf[index].render(scale=RENDER_SCALE).to_pil()
            buffer = io.BytesIO()
            pil_image.save(buffer, format="PNG")
            pages.append(buffer.getvalue())
        return pages
    except OcrError:
        raise
    except Exception as exc:  # PDFium 打不开 / 渲染失败统一可读化
        raise OcrError(f"扫描件页图渲染失败：{exc}") from exc


async def ocr_pdf(data: bytes) -> str:
    """扫描件 PDF → vision 分层多模态转写 → 纯文本（已 clean，可正常分析）。"""
    images = await asyncio.to_thread(render_pages, data)
    content: list[dict] = [{"type": "text", "text": _OCR_PROMPT}]
    for png in images:
        encoded = base64.b64encode(png).decode("ascii")
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}
        )

    # 转写是结构化任务：关思考模式（省 token/耗时），低温稳定输出
    raw = await llm.chat(
        "vision",
        [{"role": "user", "content": content}],
        temperature=0.1,
        max_tokens=6144,
        timeout=LLM_TIMEOUT_SEC,
        thinking=False,
    )
    text = resume_parser.clean(raw)
    if len(text) < resume_parser.MIN_CHARS:
        raise OcrError("OCR 未识别出有效内容，请上传更清晰的扫描件或可复制文字的版本")
    return text
