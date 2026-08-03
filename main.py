"""
知识库管理插件
功能：
1. 聊天命令：查看知识库列表、查看文档、二次确认删除
2. WebUI 插件页：独立 dashboard，支持查看 / 修改 / 删除 / 新建文档
"""

from __future__ import annotations

import time
from typing import Any

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register
from astrbot.core import AstrBotConfig
from astrbot.core.platform.astr_message_event import AstrMessageEvent


def _fmt_size(size: int | None) -> str:
    """格式化文件大小"""
    if size is None:
        return "?"
    try:
        n = int(size)
    except Exception:
        return str(size)
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / 1024 / 1024:.2f}MB"


def _short_id(s: str | None, n: int = 8) -> str:
    if not s:
        return "-"
    s = str(s)
    return s if len(s) <= n else s[:n]


@register(
    "astrbot_plugin_kb_manager",
    "沐瑶",
    "知识库管理：聊天命令 + WebUI 查看/修改/删除",
    "1.3.0",
    "",
)
class KBManagerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self._pending_delete: dict[str, dict[str, Any]] = {}
        self.page_api = None
        self._register_official_page_api_if_available()

    def _register_official_page_api_if_available(self) -> None:
        if not hasattr(self.context, "register_web_api"):
            logger.warning("[kb_manager] 当前 AstrBot 不支持 register_web_api，WebUI 页面 API 未注册")
            return
        try:
            from .page_api import PluginPageApi
            self.page_api = PluginPageApi(self)
            self.page_api.register_routes()
            logger.info("[kb_manager] 已注册知识库管理 WebUI 页面 API")
        except Exception as exc:
            self.page_api = None
            logger.warning(f"[kb_manager] 官方插件页面 API 注册失败: {exc}", exc_info=True)

    def _only_admin(self) -> bool:
        return bool(self.config.get("only_admin", True))

    def _page_size(self) -> int:
        try:
            n = int(self.config.get("page_size", 10))
        except Exception:
            n = 10
        return max(1, min(n, 50))

    def _confirm_seconds(self) -> int:
        try:
            n = int(self.config.get("delete_confirm_seconds", 60))
        except Exception:
            n = 60
        return max(10, min(n, 600))

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        try:
            return bool(event.is_admin())
        except Exception:
            try:
                admins = self.context.get_config().get("admins_id", []) or []
                uid = str(event.get_sender_id())
                return uid in [str(x) for x in admins]
            except Exception:
                return False

    def _check_perm(self, event: AstrMessageEvent) -> str | None:
        if self._only_admin() and not self._is_admin(event):
            return "这个命令只有管理员能用哦～"
        return None

    def _kb_mgr(self):
        mgr = getattr(self.context, "kb_manager", None)
        if mgr is None:
            raise RuntimeError("当前环境未加载知识库管理器（context.kb_manager 为空）")
        return mgr

    async def _list_kb_helpers(self) -> list:
        mgr = self._kb_mgr()
        items = list(getattr(mgr, "kb_insts", {}).values())
        if items:
            return items
        kbs = await mgr.list_kbs()
        helpers = []
        for kb in kbs:
            h = await mgr.get_kb(kb.kb_id)
            if h:
                helpers.append(h)
        return helpers

    async def _resolve_kb(self, token: str):
        token = (token or "").strip()
        if not token:
            return None, "请给出知识库名称或编号。"
        helpers = await self._list_kb_helpers()
        if not helpers:
            return None, "当前还没有知识库。"
        if token.isdigit():
            idx = int(token)
            if 1 <= idx <= len(helpers):
                return helpers[idx - 1], None
            return None, f"编号无效，当前共 {len(helpers)} 个知识库。"
        mgr = self._kb_mgr()
        by_id = await mgr.get_kb(token)
        if by_id:
            return by_id, None
        by_name = await mgr.get_kb_by_name(token)
        if by_name:
            return by_name, None
        matches = [h for h in helpers if token in (h.kb.kb_name or "") or token in (h.kb.kb_id or "")]
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            names = "、".join(h.kb.kb_name for h in matches[:8])
            return None, f"匹配到多个知识库：{names}，请写更准确的名字或用编号。"
        return None, f"找不到知识库：{token}"

    async def _resolve_doc(self, kb_helper, token: str, page_hint: int | None = None):
        token = (token or "").strip()
        if not token:
            return None, "请给出文档编号、文档ID 或文件名。"
        try:
            doc = await kb_helper.get_document(token)
            if doc:
                return doc, None
        except Exception:
            pass
        docs = await kb_helper.list_documents(offset=0, limit=500)
        if not docs:
            return None, "这个知识库里还没有文档。"
        if token.isdigit():
            idx = int(token)
            if 1 <= idx <= len(docs):
                return docs[idx - 1], None
            return None, f"文档编号无效，当前可读到 {len(docs)} 个文档。"
        exact = [d for d in docs if (d.doc_name or "") == token]
        if len(exact) == 1:
            return exact[0], None
        if len(exact) > 1:
            return None, f"存在多个同名文件「{token}」，请用文档ID删除。"
        partial = [d for d in docs if token in (d.doc_name or "")]
        if len(partial) == 1:
            return partial[0], None
        if len(partial) > 1:
            names = "、".join((d.doc_name or d.doc_id)[:40] for d in partial[:6])
            return None, f"匹配到多个文件：{names}，请写更完整的名字或用编号/ID。"
        id_hits = [d for d in docs if (d.doc_id or "").startswith(token)]
        if len(id_hits) == 1:
            return id_hits[0], None
        if len(id_hits) > 1:
            return None, "文档ID前缀不唯一，请给更长一点。"
        return None, f"找不到文档：{token}"

    async def _collect_delete_targets(self, kb_helper, tokens: list[str]):
        docs = await kb_helper.list_documents(offset=0, limit=500)
        if not docs:
            return None, "这个知识库里还没有文档。"
        targets: dict[str, str] = {}
        for tok in tokens:
            tok = tok.strip()
            if not tok:
                continue
            if tok.isdigit():
                idx = int(tok)
                if 1 <= idx <= len(docs):
                    d = docs[idx - 1]
                    targets[d.doc_id] = d.doc_name
                else:
                    return None, f"编号 {tok} 无效，当前可读到 {len(docs)} 个文档。"
                continue
            hit = [d for d in docs if (d.doc_id or "") == tok]
            if len(hit) == 1:
                targets[hit[0].doc_id] = hit[0].doc_name
                continue
            hit = [d for d in docs if (d.doc_name or "") == tok]
            if len(hit) == 1:
                targets[hit[0].doc_id] = hit[0].doc_name
                continue
            if len(hit) > 1:
                return None, f"存在多个同名文件「{tok}」，请用文档ID指定。"
            hit = [d for d in docs if (d.doc_id or "").startswith(tok)]
            if len(hit) == 1:
                targets[hit[0].doc_id] = hit[0].doc_name
                continue
            if len(hit) > 1:
                return None, f"文档ID前缀「{tok}」不唯一，请给更长一点。"
            hit = [d for d in docs if tok in (d.doc_name or "")]
            if len(hit) == 1:
                targets[hit[0].doc_id] = hit[0].doc_name
                continue
            if len(hit) > 1:
                names = "、".join((d.doc_name or d.doc_id)[:40] for d in hit[:6])
                return None, f"「{tok}」匹配到多个文件：{names}，请写更完整的名字或用编号/ID。"
            return None, f"找不到文档：{tok}"
        if not targets:
            return None, "没有可删除的文档，请检查给出的条件。"
        return [{"doc_id": k, "doc_name": v} for k, v in targets.items()], None

    def _pending_key(self, event: AstrMessageEvent) -> str:
        return f"{event.unified_msg_origin}:{event.get_sender_id()}"

    def _clean_expired_pending(self) -> None:
        now = time.time()
        expired = [k for k, v in self._pending_delete.items() if v.get("expire_at", 0) < now]
        for k in expired:
            self._pending_delete.pop(k, None)

    @filter.command("知识库帮助", alias={"kb帮助", "知识库管理帮助"})
    async def cmd_help(self, event: AstrMessageEvent):
        deny = self._check_perm(event)
        if deny:
            yield event.plain_result(deny)
            return
        text = (
            "📚 知识库管理帮助\n"
            "· 知识库列表\n"
            "· 知识库文件 <名称|编号> [页码]\n"
            "· 知识库详情 <名称|编号> <文档编号|ID|文件名>\n"
            "· 知识库下载文件 <名称|编号> <文档编号|ID|文件名>\n"
            "· 知识库删除文件 <名称|编号> <文档编号|ID|文件名>\n"
            "· 知识库批量删除 <名称|编号> <编号/ID/文件名...> 或 匹配 <关键词>\n"
            "· 知识库确认删除\n"
            "· 知识库取消删除\n"
            "· 知识库界面：查看 WebUI 入口说明\n"
            "WebUI：插件 → 知识库管理 → Pages → dashboard\n"
            "提示：删除需二次确认，请谨慎操作。"
        )
        yield event.plain_result(text)

    @filter.command("知识库界面", alias={"kb界面", "知识库webui", "知识库UI"})
    async def cmd_ui(self, event: AstrMessageEvent):
        deny = self._check_perm(event)
        if deny:
            yield event.plain_result(deny)
            return
        has_api = self.page_api is not None
        text = (
            "🖥️ 知识库管理 WebUI\n"
            "打开方式：AstrBot WebUI → 插件 → 知识库管理 → Pages → dashboard\n"
            "可在页面中：查看知识库/文档、搜索、编辑正文、删除、新建纯文本文档。\n"
            f"页面 API：{'已注册' if has_api else '未注册（当前环境可能不支持）'}\n"
            "说明：保存修改会删除旧文档并以新内容重新入库（重建向量）。"
        )
        yield event.plain_result(text)

    @filter.command("知识库列表", alias={"kb列表", "知识库一览"})
    async def cmd_list_kb(self, event: AstrMessageEvent):
        deny = self._check_perm(event)
        if deny:
            yield event.plain_result(deny)
            return
        try:
            helpers = await self._list_kb_helpers()
        except Exception as e:
            logger.error(f"[kb_manager] 列出知识库失败: {e}", exc_info=True)
            yield event.plain_result(f"列出知识库失败：{e}")
            return
        if not helpers:
            yield event.plain_result("当前还没有知识库～")
            return
        lines = [f"📚 知识库列表（共 {len(helpers)} 个）"]
        for i, h in enumerate(helpers, 1):
            kb = h.kb
            emoji = kb.emoji or "📘"
            name = kb.kb_name or "(未命名)"
            desc = (kb.description or "").strip()
            if len(desc) > 40:
                desc = desc[:40] + "…"
            err = getattr(h, "init_error", None)
            status = " ⚠️初始化异常" if err else ""
            lines.append(f"{i}. {emoji} {name}\n   文档 {kb.doc_count} · 块 {kb.chunk_count} · ID {_short_id(kb.kb_id)}{status}")
            if desc:
                lines.append(f"   说明：{desc}")
        lines.append("\n用法：知识库文件 <名称或编号>")
        lines.append("WebUI：知识库界面")
        yield event.plain_result("\n".join(lines))

    @filter.command("知识库文件", alias={"kb文件", "知识库文档", "查看知识库文件"})
    async def cmd_list_docs(self, event: AstrMessageEvent):
        deny = self._check_perm(event)
        if deny:
            yield event.plain_result(deny)
            return
        raw = (event.message_str or "").strip()
        for prefix in ("知识库文件", "kb文件", "知识库文档", "查看知识库文件"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):].strip()
                break
        parts = raw.split()
        if not parts:
            yield event.plain_result("用法：知识库文件 <知识库名称|编号> [页码]")
            return
        kb_token = parts[0]
        page = 1
        if len(parts) >= 2 and parts[1].isdigit():
            page = max(1, int(parts[1]))
        kb_helper, err = await self._resolve_kb(kb_token)
        if err:
            yield event.plain_result(err)
            return
        page_size = self._page_size()
        offset = (page - 1) * page_size
        try:
            total = await kb_helper.count_documents()
            docs = await kb_helper.list_documents(offset=offset, limit=page_size)
        except Exception as e:
            logger.error(f"[kb_manager] 列出文档失败: {e}", exc_info=True)
            yield event.plain_result(f"读取文档列表失败：{e}")
            return
        kb = kb_helper.kb
        total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
        if page > total_pages and total:
            yield event.plain_result(f"页码超出范围，共 {total_pages} 页。")
            return
        lines = [f"📁 {kb.emoji or '📘'} {kb.kb_name} 的文件", f"共 {total} 个 · 第 {page}/{total_pages} 页"]
        if not docs:
            lines.append("（这一页没有文档）")
        else:
            for i, d in enumerate(docs, start=offset + 1):
                lines.append(f"{i}. {d.doc_name}\n   类型 {d.file_type} · {_fmt_size(d.file_size)} · 块 {d.chunk_count} · ID {_short_id(d.doc_id)}")
        lines.append("\n详情：知识库详情 <库> <编号|ID|文件名>\n下载：知识库下载文件 <库> <编号|ID|文件名>\n删除：知识库删除文件 <库> <编号|ID|文件名>\n批量删除：知识库批量删除 <库> 匹配 <关键词>\n更方便：知识库界面（WebUI 可直接改）")
        if page < total_pages:
            lines.append(f"下一页：知识库文件 {kb.kb_name} {page + 1}")
        yield event.plain_result("\n".join(lines))

    @filter.command("知识库详情", alias={"kb详情", "知识库文档详情"})
    async def cmd_doc_detail(self, event: AstrMessageEvent):
        deny = self._check_perm(event)
        if deny:
            yield event.plain_result(deny)
            return
        raw = (event.message_str or "").strip()
        for prefix in ("知识库详情", "kb详情", "知识库文档详情"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):].strip()
                break
        parts = raw.split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("用法：知识库详情 <知识库名称|编号> <文档编号|ID|文件名>")
            return
        kb_helper, err = await self._resolve_kb(parts[0])
        if err:
            yield event.plain_result(err)
            return
        doc, err = await self._resolve_doc(kb_helper, parts[1])
        if err:
            yield event.plain_result(err)
            return
        created = getattr(doc, "created_at", None)
        updated = getattr(doc, "updated_at", None)
        lines = [f"📄 文档详情 · {kb_helper.kb.kb_name}", f"名称：{doc.doc_name}", f"ID：{doc.doc_id}", f"类型：{doc.file_type}", f"大小：{_fmt_size(doc.file_size)}", f"分块数：{doc.chunk_count}", f"媒体数：{getattr(doc, 'media_count', 0)}", f"路径：{doc.file_path}"]
        if created:
            lines.append(f"创建：{created}")
        if updated:
            lines.append(f"更新：{updated}")
        lines.append("修改建议：打开「知识库界面」WebUI 直接编辑正文。")
        yield event.plain_result("\n".join(lines))

    @filter.command("知识库下载文件", alias={"kb下载文件", "下载知识库文件", "kb下载"})
    async def cmd_download_doc(self, event: AstrMessageEvent):
        deny = self._check_perm(event)
        if deny:
            yield event.plain_result(deny)
            return
        raw = (event.message_str or "").strip()
        for prefix in ("知识库下载文件", "kb下载文件", "下载知识库文件", "kb下载"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):].strip()
                break
        parts = raw.split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("用法：知识库下载文件 <知识库名称|编号> <文档编号|ID|文件名>")
            return
        kb_helper, err = await self._resolve_kb(parts[0])
        if err:
            yield event.plain_result(err)
            return
        if getattr(kb_helper, "init_error", None):
            yield event.plain_result(f"该知识库初始化异常：{kb_helper.init_error}")
            return
        doc, err = await self._resolve_doc(kb_helper, parts[1])
        if err:
            yield event.plain_result(err)
            return
        if self.page_api is None:
            yield event.plain_result("页面 API 未注册，无法下载。")
            return
        try:
            content, _ = await self.page_api._load_content(kb_helper, doc.doc_id)
            if not content.strip():
                yield event.plain_result("文档内容为空。")
                return
            import tempfile
            from pathlib import Path
            from werkzeug.utils import secure_filename
            file_name = doc.doc_name or f"{doc.doc_id}.txt"
            safe_name = secure_filename(file_name) or "download.txt"
            tmp = Path(tempfile.gettempdir()) / safe_name
            tmp.write_text(content, encoding="utf-8")
            yield event.chain_result([{"type": "file", "path": str(tmp)}])
        except Exception as e:
            logger.error(f"[kb_manager] 下载文档失败: {e}", exc_info=True)
            yield event.plain_result(f"下载失败：{e}")

    @filter.command("知识库删除文件", alias={"kb删除文件", "删除知识库文件", "知识库删文件"})
    async def cmd_delete_doc(self, event: AstrMessageEvent):
        deny = self._check_perm(event)
        if deny:
            yield event.plain_result(deny)
            return
        raw = (event.message_str or "").strip()
        for prefix in ("知识库删除文件", "kb删除文件", "删除知识库文件", "知识库删文件"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):].strip()
                break
        parts = raw.split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("用法：知识库删除文件 <知识库名称|编号> <文档编号|ID|文件名>\n删除前会要求你发送「知识库确认删除」。")
            return
        kb_helper, err = await self._resolve_kb(parts[0])
        if err:
            yield event.plain_result(err)
            return
        if getattr(kb_helper, "init_error", None):
            yield event.plain_result(f"该知识库初始化异常，暂不能删除：{kb_helper.init_error}")
            return
        doc, err = await self._resolve_doc(kb_helper, parts[1])
        if err:
            yield event.plain_result(err)
            return
        self._clean_expired_pending()
        key = self._pending_key(event)
        expire = time.time() + self._confirm_seconds()
        self._pending_delete[key] = {"kb_id": kb_helper.kb.kb_id, "kb_name": kb_helper.kb.kb_name, "docs": [{"doc_id": doc.doc_id, "doc_name": doc.doc_name}], "expire_at": expire, "sender_id": str(event.get_sender_id())}
        yield event.plain_result(f"⚠️ 即将删除知识库文档（不可恢复）\n知识库：{kb_helper.kb.kb_name}\n文件：{doc.doc_name}\nID：{doc.doc_id}\n请在 {self._confirm_seconds()} 秒内发送：知识库确认删除\n取消请发：知识库取消删除")

    @filter.command("知识库批量删除", alias={"kb批量删除", "批量删除知识库文件", "知识库批量删"})
    async def cmd_batch_delete(self, event: AstrMessageEvent):
        deny = self._check_perm(event)
        if deny:
            yield event.plain_result(deny)
            return
        raw = (event.message_str or "").strip()
        for prefix in ("知识库批量删除", "kb批量删除", "批量删除知识库文件", "知识库批量删"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):].strip()
                break
        parts = raw.split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("用法：知识库批量删除 <知识库名称|编号> <文档编号/ID/文件名...>\n或：知识库批量删除 <知识库名称|编号> 匹配 <关键词>\n删除前会要求你发送「知识库确认删除」。")
            return
        kb_helper, err = await self._resolve_kb(parts[0])
        if err:
            yield event.plain_result(err)
            return
        if getattr(kb_helper, "init_error", None):
            yield event.plain_result(f"该知识库初始化异常，暂不能删除：{kb_helper.init_error}")
            return
        rest = parts[1].strip()
        if rest.startswith(("匹配", "match", "含")):
            keyword = rest
            for pre in ("匹配", "match", "含"):
                if keyword.startswith(pre):
                    keyword = keyword[len(pre):].lstrip(" ：:")
                    break
            if not keyword:
                yield event.plain_result("用法：知识库批量删除 <库> 匹配 <关键词>")
                return
            docs = await kb_helper.list_documents(offset=0, limit=500)
            if not docs:
                yield event.plain_result("这个知识库里还没有文档。")
                return
            matched = [d for d in docs if keyword in (d.doc_name or "")]
            if not matched:
                yield event.plain_result(f"没有找到文件名包含「{keyword}」的文档。")
                return
            targets = [{"doc_id": d.doc_id, "doc_name": d.doc_name} for d in matched]
        else:
            tokens = rest.split()
            targets, err2 = await self._collect_delete_targets(kb_helper, tokens)
            if err2:
                yield event.plain_result(err2)
                return
        if not targets:
            yield event.plain_result("没有可删除的文档，请检查给出的条件。")
            return
        if len(targets) > 50:
            yield event.plain_result(f"一次最多批量删除 50 个文档，当前选了 {len(targets)} 个，请缩小范围。")
            return
        self._clean_expired_pending()
        key = self._pending_key(event)
        expire = time.time() + self._confirm_seconds()
        self._pending_delete[key] = {"kb_id": kb_helper.kb.kb_id, "kb_name": kb_helper.kb.kb_name, "docs": targets, "expire_at": expire, "sender_id": str(event.get_sender_id())}
        lines = [f"⚠️ 即将批量删除知识库文档（不可恢复）", f"知识库：{kb_helper.kb.kb_name}", f"共 {len(targets)} 个文件："]
        for i, t in enumerate(targets, 1):
            lines.append(f"{i}. {t.get('doc_name', t.get('doc_id', ''))}")
        lines.append(f"请在 {self._confirm_seconds()} 秒内发送：知识库确认删除")
        lines.append("取消请发：知识库取消删除")
        yield event.plain_result("\n".join(lines))

    @filter.command("知识库确认删除", alias={"kb确认删除", "确认删除知识库文件"})
    async def cmd_confirm_delete(self, event: AstrMessageEvent):
        deny = self._check_perm(event)
        if deny:
            yield event.plain_result(deny)
            return
        self._clean_expired_pending()
        key = self._pending_key(event)
        pending = self._pending_delete.get(key)
        if not pending:
            yield event.plain_result("没有待确认的删除，或已超时。请重新执行「知识库删除文件」。")
            return
        kb_id = pending["kb_id"]
        kb_name = pending.get("kb_name", kb_id)
        docs = pending.get("docs") or [{"doc_id": pending.get("doc_id"), "doc_name": pending.get("doc_name", pending.get("doc_id"))}]
        try:
            mgr = self._kb_mgr()
            kb_helper = await mgr.get_kb(kb_id)
            if not kb_helper:
                self._pending_delete.pop(key, None)
                yield event.plain_result(f"知识库已不存在：{kb_name}")
                return
            ok_list = []
            fail_list = []
            for item in docs:
                doc_id = item.get("doc_id")
                doc_name = item.get("doc_name", doc_id)
                if not doc_id:
                    fail_list.append(f"{doc_name or '未知'}(缺少ID)")
                    continue
                try:
                    await kb_helper.delete_document(doc_id)
                    ok_list.append(doc_name or doc_id)
                except Exception as de:
                    logger.error(f"[kb_manager] 批量删除单个失败: {de}", exc_info=True)
                    fail_list.append(f"{doc_name or doc_id}({de})")
            self._pending_delete.pop(key, None)
            try:
                await kb_helper.refresh_kb()
            except Exception:
                pass
            lines = [f"✅ 批量删除完成 · 知识库：{kb_name}"]
            if ok_list:
                lines.append(f"成功 {len(ok_list)} 个：")
                lines.extend(f"· {n}" for n in ok_list[:30])
                if len(ok_list) > 30:
                    lines.append(f"… 等共 {len(ok_list)} 个")
            if fail_list:
                lines.append(f"失败 {len(fail_list)} 个：")
                lines.extend(f"· {n}" for n in fail_list[:10])
            logger.info(f"[kb_manager] 用户 {event.get_sender_id()} 批量删除 kb={kb_id} ok={len(ok_list)} fail={len(fail_list)}")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"[kb_manager] 批量删除失败: {e}", exc_info=True)
            yield event.plain_result(f"删除失败：{e}")

    @filter.command("知识库取消删除", alias={"kb取消删除", "取消删除知识库文件"})
    async def cmd_cancel_delete(self, event: AstrMessageEvent):
        deny = self._check_perm(event)
        if deny:
            yield event.plain_result(deny)
            return
        key = self._pending_key(event)
        if key in self._pending_delete:
            p = self._pending_delete.pop(key)
            docs = p.get("docs") or [{"doc_id": p.get("doc_id"), "doc_name": p.get("doc_name", p.get("doc_id"))}]
            names = "、".join((d.get("doc_name") or d.get("doc_id") or "未知") for d in docs)
            yield event.plain_result(f"已取消删除：{names}")
        else:
            yield event.plain_result("当前没有待确认的删除。")