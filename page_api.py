"""知识库管理插件 - 官方插件 Page API。

提供 WebUI 所需的列表 / 查看 / 编辑 / 删除接口。
文档“修改”采用：读取文本块 -> 用户改写 -> 删除旧文档 -> 以新内容重新入库。
"""

from __future__ import annotations

from typing import Any

import zipfile
from io import BytesIO

from astrbot.api import logger
from quart import request
from starlette.responses import Response as StarletteResponse
from urllib.parse import quote

PLUGIN_NAME = "astrbot_plugin_kb_manager"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"


def _ok(data: Any = None) -> dict[str, Any]:
    return {"status": "ok", "data": data}


def _error(message: str) -> dict[str, Any]:
    return {"status": "error", "message": str(message)}


def _download_response(data: bytes, media_type: str, filename: str) -> StarletteResponse:
    """构造下载响应。文件名用 RFC 5987 百分号编码，header 保持纯 ASCII，
    避免 quart 旧版 send_file 的 latin-1 header 编码在框架转换时报错。"""
    quoted = quote(filename, safe="")
    disposition = f"attachment; filename=\"download.bin\"; filename*=UTF-8''{quoted}"
    return StarletteResponse(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": disposition},
    )


def _safe_file_name(name: str) -> str:
    """轻量文件名清洗：保留中文，避免 secure_filename 把中文删光。"""
    name = (name or "").replace("\\", "_").replace("/", "_")
    name = "".join(ch for ch in name if ch not in '<>:"|?*')
    return name.strip(" .") or "download.txt"


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _doc_to_dict(doc) -> dict[str, Any]:
    if hasattr(doc, "model_dump"):
        data = doc.model_dump()
    else:
        data = {
            "doc_id": getattr(doc, "doc_id", None),
            "kb_id": getattr(doc, "kb_id", None),
            "doc_name": getattr(doc, "doc_name", None),
            "file_type": getattr(doc, "file_type", None),
            "file_size": getattr(doc, "file_size", None),
            "file_path": getattr(doc, "file_path", None),
            "chunk_count": getattr(doc, "chunk_count", 0),
            "media_count": getattr(doc, "media_count", 0),
            "created_at": getattr(doc, "created_at", None),
            "updated_at": getattr(doc, "updated_at", None),
        }
    for key in ("created_at", "updated_at"):
        val = data.get(key)
        if hasattr(val, "isoformat"):
            data[key] = val.isoformat()
    return data


def _kb_to_dict(helper) -> dict[str, Any]:
    kb = helper.kb
    if hasattr(kb, "model_dump"):
        data = kb.model_dump()
    else:
        data = {
            "kb_id": getattr(kb, "kb_id", None),
            "kb_name": getattr(kb, "kb_name", None),
            "description": getattr(kb, "description", None),
            "emoji": getattr(kb, "emoji", "📘"),
            "doc_count": getattr(kb, "doc_count", 0),
            "chunk_count": getattr(kb, "chunk_count", 0),
        }
    for key in ("created_at", "updated_at"):
        val = data.get(key)
        if hasattr(val, "isoformat"):
            data[key] = val.isoformat()
    data["init_error"] = getattr(helper, "init_error", None)
    return data


class PluginPageApi:
    """官方插件页面 API。"""

    def __init__(self, plugin) -> None:
        self.plugin = plugin

    def register_routes(self) -> None:
        register = self.plugin.context.register_web_api
        routes = [
            (f"{PAGE_API_PREFIX}/ping", self.ping, ["GET"], "KB Manager ping"),
            (f"{PAGE_API_PREFIX}/kbs", self.list_kbs, ["GET"], "KB Manager list kbs"),
            (
                f"{PAGE_API_PREFIX}/documents",
                self.list_documents,
                ["GET"],
                "KB Manager list documents",
            ),
            (
                f"{PAGE_API_PREFIX}/document",
                self.get_document,
                ["GET"],
                "KB Manager get document",
            ),
            (
                f"{PAGE_API_PREFIX}/document/content",
                self.get_document_content,
                ["GET"],
                "KB Manager get document content",
            ),
            (
                f"{PAGE_API_PREFIX}/document/update",
                self.update_document,
                ["POST"],
                "KB Manager update document",
            ),
            (
                f"{PAGE_API_PREFIX}/document/delete",
                self.delete_document,
                ["POST"],
                "KB Manager delete document",
            ),
            (
                f"{PAGE_API_PREFIX}/document/batch_delete",
                self.batch_delete_document,
                ["POST"],
                "KB Manager batch delete documents",
            ),
            (
                f"{PAGE_API_PREFIX}/document/create",
                self.create_document,
                ["POST"],
                "KB Manager create document",
            ),
            (
                f"{PAGE_API_PREFIX}/document/download",
                self.download_document,
                ["GET"],
                "KB Manager download document as file",
            ),
            (
                f"{PAGE_API_PREFIX}/document/batch_download",
                self.batch_download_documents,
                ["GET", "POST"],
                "KB Manager batch download documents as zip",
            ),
        ]
        for route, handler, methods, desc in routes:
            register(route, handler, methods, desc)

    async def ping(self):
        return _ok({"message": "pong", "plugin": PLUGIN_NAME, "version": "1.2.0"})

    def _kb_mgr(self):
        mgr = getattr(self.plugin.context, "kb_manager", None)
        if mgr is None:
            raise RuntimeError("当前环境未加载知识库管理器（context.kb_manager 为空）")
        return mgr

    async def _list_helpers(self) -> list:
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

    async def _get_helper(self, kb_id: str):
        if not kb_id:
            raise ValueError("缺少 kb_id")
        helper = await self._kb_mgr().get_kb(kb_id)
        if not helper:
            raise ValueError("知识库不存在")
        return helper

    async def list_kbs(self):
        try:
            helpers = await self._list_helpers()
            items = [_kb_to_dict(h) for h in helpers]
            return _ok({"items": items, "total": len(items)})
        except Exception as e:
            logger.error(f"[kb_manager] list_kbs failed: {e}", exc_info=True)
            return _error(str(e))

    async def list_documents(self):
        try:
            args = request.args
            kb_id = (args.get("kb_id") or "").strip()
            page = max(1, _to_int(args.get("page"), 1))
            page_size = max(1, min(100, _to_int(args.get("page_size"), 20)))
            search = (args.get("search") or "").strip() or None

            helper = await self._get_helper(kb_id)
            offset = (page - 1) * page_size
            docs = await helper.list_documents(
                offset=offset, limit=page_size, search=search
            )
            total = await helper.count_documents(search=search)
            return _ok(
                {
                    "items": [_doc_to_dict(d) for d in docs],
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "kb": _kb_to_dict(helper),
                }
            )
        except Exception as e:
            logger.error(f"[kb_manager] list_documents failed: {e}", exc_info=True)
            return _error(str(e))

    async def get_document(self):
        try:
            args = request.args
            kb_id = (args.get("kb_id") or "").strip()
            doc_id = (args.get("doc_id") or "").strip()
            if not doc_id:
                return _error("缺少 doc_id")
            helper = await self._get_helper(kb_id)
            doc = await helper.get_document(doc_id)
            if not doc:
                return _error("文档不存在")
            return _ok({"document": _doc_to_dict(doc), "kb": _kb_to_dict(helper)})
        except Exception as e:
            logger.error(f"[kb_manager] get_document failed: {e}", exc_info=True)
            return _error(str(e))

    async def _load_content(self, helper, doc_id: str) -> tuple[str, list[dict]]:
        total = await helper.get_chunk_count_by_doc_id(doc_id)
        if total <= 0:
            return "", []
        # 分批拉取全部文本块
        chunks: list[dict] = []
        page_size = 200
        offset = 0
        while offset < total:
            batch = await helper.get_chunks_by_doc_id(
                doc_id=doc_id, offset=offset, limit=page_size
            )
            if not batch:
                break
            chunks.extend(batch)
            offset += len(batch)
            if len(batch) < page_size:
                break
        chunks.sort(key=lambda x: int(x.get("chunk_index") or 0))
        # 文本块通常按字符切分，直接拼接更接近原文；块间重复重叠无法完美还原，但足够编辑
        content = "".join(str(c.get("content") or "") for c in chunks)
        return content, chunks

    async def get_document_content(self):
        try:
            args = request.args
            kb_id = (args.get("kb_id") or "").strip()
            doc_id = (args.get("doc_id") or "").strip()
            if not doc_id:
                return _error("缺少 doc_id")
            helper = await self._get_helper(kb_id)
            doc = await helper.get_document(doc_id)
            if not doc:
                return _error("文档不存在")
            content, chunks = await self._load_content(helper, doc_id)
            return _ok(
                {
                    "document": _doc_to_dict(doc),
                    "content": content,
                    "chunk_count": len(chunks),
                    "kb": _kb_to_dict(helper),
                    "note": "正文由向量文本块拼接还原；若原文件含复杂格式，编辑后会按纯文本重新分块入库。",
                }
            )
        except Exception as e:
            logger.error(
                f"[kb_manager] get_document_content failed: {e}", exc_info=True
            )
            return _error(str(e))

    async def update_document(self):
        """修改文档：删旧重建（原生 KB 无原地更新）。"""
        try:
            payload = await request.get_json(silent=True) or {}
            kb_id = str(payload.get("kb_id") or "").strip()
            doc_id = str(payload.get("doc_id") or "").strip()
            content = payload.get("content")
            new_name = str(payload.get("doc_name") or "").strip()

            if not kb_id or not doc_id:
                return _error("缺少 kb_id 或 doc_id")
            if content is None:
                return _error("缺少 content")
            content_text = str(content)
            if not content_text.strip():
                return _error("正文不能为空")

            helper = await self._get_helper(kb_id)
            if getattr(helper, "init_error", None):
                return _error(f"知识库初始化异常：{helper.init_error}")

            doc = await helper.get_document(doc_id)
            if not doc:
                return _error("文档不存在")

            old_name = doc.doc_name or "document.txt"
            file_name = new_name or old_name
            # 保持扩展名，便于解析器识别
            if "." not in file_name:
                ext = (doc.file_type or "txt").lstrip(".")
                file_name = f"{file_name}.{ext or 'txt'}"
            file_type = (
                file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "txt"
            )
            if file_type not in {"txt", "md", "markdown", "mkd", "mdx"}:
                # 编辑界面按纯文本处理，统一落到 txt/md
                file_type = "txt" if not file_name.endswith(".md") else "md"
                if not file_name.lower().endswith((".txt", ".md")):
                    file_name = f"{file_name}.txt"
                    file_type = "txt"

            chunk_size = getattr(helper.kb, "chunk_size", None) or 512
            chunk_overlap = getattr(helper.kb, "chunk_overlap", None) or 50

            # 先上传新文档，成功后再删旧文档，避免失败丢数据
            new_doc = await helper.upload_document(
                file_name=file_name,
                file_content=content_text.encode("utf-8"),
                file_type=file_type,
                chunk_size=int(chunk_size),
                chunk_overlap=int(chunk_overlap),
            )
            try:
                await helper.delete_document(doc_id)
            except Exception as del_err:
                # 新文档已入库，旧文档删除失败时提示两边可能并存
                logger.error(
                    f"[kb_manager] update: new uploaded but old delete failed: {del_err}",
                    exc_info=True,
                )
                return _error(
                    f"新文档已保存，但旧文档删除失败（可能暂时并存）：{del_err}"
                )

            try:
                await helper.refresh_kb()
            except Exception:
                pass

            return _ok(
                {
                    "message": "文档已更新（已重建索引）",
                    "old_doc_id": doc_id,
                    "document": _doc_to_dict(new_doc),
                }
            )
        except Exception as e:
            logger.error(f"[kb_manager] update_document failed: {e}", exc_info=True)
            return _error(str(e))

    async def create_document(self):
        """新建纯文本文档。"""
        try:
            payload = await request.get_json(silent=True) or {}
            kb_id = str(payload.get("kb_id") or "").strip()
            content = payload.get("content")
            file_name = str(payload.get("doc_name") or "").strip()
            if not kb_id:
                return _error("缺少 kb_id")
            if content is None or not str(content).strip():
                return _error("正文不能为空")
            if not file_name:
                return _error("缺少文档名称")
            if "." not in file_name:
                file_name = f"{file_name}.txt"
            file_type = file_name.rsplit(".", 1)[-1].lower()
            if file_type not in {"txt", "md", "markdown", "mkd", "mdx"}:
                file_type = "txt"

            helper = await self._get_helper(kb_id)
            if getattr(helper, "init_error", None):
                return _error(f"知识库初始化异常：{helper.init_error}")

            chunk_size = getattr(helper.kb, "chunk_size", None) or 512
            chunk_overlap = getattr(helper.kb, "chunk_overlap", None) or 50
            new_doc = await helper.upload_document(
                file_name=file_name,
                file_content=str(content).encode("utf-8"),
                file_type=file_type,
                chunk_size=int(chunk_size),
                chunk_overlap=int(chunk_overlap),
            )
            return _ok({"message": "文档已创建", "document": _doc_to_dict(new_doc)})
        except Exception as e:
            logger.error(f"[kb_manager] create_document failed: {e}", exc_info=True)
            return _error(str(e))

    async def download_document(self):
        """下载文档为文件（正文由向量块重建）。"""
        try:
            args = request.args
            kb_id = (args.get("kb_id") or "").strip()
            doc_id = (args.get("doc_id") or "").strip()
            if not kb_id or not doc_id:
                return _error("缺少 kb_id 或 doc_id")
            helper = await self._get_helper(kb_id)
            if getattr(helper, "init_error", None):
                return _error(f"知识库初始化异常：{helper.init_error}")
            doc = await helper.get_document(doc_id)
            if not doc:
                return _error("文档不存在")

            content, _ = await self._load_content(helper, doc_id)
            if not content.strip():
                return _error("文档内容为空，无法下载")

            file_name = doc.doc_name or f"{doc_id}.txt"
            safe_name = _safe_file_name(file_name)
            return _download_response(
                content.encode("utf-8"),
                "application/octet-stream",
                safe_name,
            )
        except Exception as e:
            logger.error(f"[kb_manager] download_document failed: {e}", exc_info=True)
            return _error(str(e))

    async def batch_download_documents(self):
        """批量下载文档为 zip 压缩包。支持 GET(query) 与 POST(json)。"""
        try:
            args = request.args
            kb_id = (args.get("kb_id") or "").strip()
            raw_ids = args.get("doc_ids") or ""
            if kb_id and raw_ids:
                doc_ids = [x.strip() for x in raw_ids.split(",") if x.strip()]
            else:
                payload = await request.get_json(silent=True) or {}
                kb_id = str(payload.get("kb_id") or "").strip()
                ids = payload.get("doc_ids") or []
                doc_ids = (
                    [str(x).strip() for x in ids if str(x).strip()]
                    if isinstance(ids, list)
                    else [x.strip() for x in str(ids).split(",") if x.strip()]
                )
            if not kb_id:
                return _error("缺少 kb_id")
            if not doc_ids:
                return _error("缺少 doc_ids")
            if len(doc_ids) > 50:
                return _error("一次最多下载 50 个文档")

            helper = await self._get_helper(kb_id)
            if getattr(helper, "init_error", None):
                return _error(f"知识库初始化异常：{helper.init_error}")

            buf = BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for doc_id in doc_ids:
                    try:
                        doc = await helper.get_document(doc_id)
                        if not doc:
                            continue
                        content, _ = await self._load_content(helper, doc_id)
                        if not content.strip():
                            continue
                        file_name = doc.doc_name or f"{doc_id}.txt"
                        safe_name = _safe_file_name(file_name)
                        zf.writestr(safe_name, content.encode("utf-8"))
                    except Exception as de:
                        logger.warning(f"[kb_manager] batch_download skip {doc_id}: {de}")
                        continue

            buf.seek(0)
            kb_name = (helper.kb.kb_name or "知识库").replace(" ", "_")
            zip_name = f"{kb_name}_批量下载.zip"
            return _download_response(buf.getvalue(), "application/zip", zip_name)
        except Exception as e:
            logger.error(f"[kb_manager] batch_download_documents failed: {e}", exc_info=True)
            return _error(str(e))

    async def delete_document(self):
        try:
            payload = await request.get_json(silent=True) or {}
            kb_id = str(payload.get("kb_id") or "").strip()
            doc_id = str(payload.get("doc_id") or "").strip()
            if not kb_id or not doc_id:
                return _error("缺少 kb_id 或 doc_id")
            helper = await self._get_helper(kb_id)
            if getattr(helper, "init_error", None):
                return _error(f"知识库初始化异常：{helper.init_error}")
            doc = await helper.get_document(doc_id)
            if not doc:
                return _error("文档不存在")
            name = doc.doc_name
            await helper.delete_document(doc_id)
            return _ok({"message": "删除成功", "doc_id": doc_id, "doc_name": name})
        except Exception as e:
            logger.error(f"[kb_manager] delete_document failed: {e}", exc_info=True)
            return _error(str(e))

    async def batch_delete_document(self):
        """批量删除：body 传 kb_id + doc_ids 数组。"""
        try:
            payload = await request.get_json(silent=True) or {}
            kb_id = str(payload.get("kb_id") or "").strip()
            doc_ids = payload.get("doc_ids") or []
            if not kb_id:
                return _error("缺少 kb_id")
            if not isinstance(doc_ids, list) or not doc_ids:
                return _error("缺少 doc_ids 数组")
            doc_ids = [str(x).strip() for x in doc_ids if str(x).strip()]
            if not doc_ids:
                return _error("doc_ids 为空")
            if len(doc_ids) > 50:
                return _error("一次最多删除 50 个文档")

            helper = await self._get_helper(kb_id)
            if getattr(helper, "init_error", None):
                return _error(f"知识库初始化异常：{helper.init_error}")

            ok_list = []
            fail_list = []
            for doc_id in doc_ids:
                try:
                    doc = await helper.get_document(doc_id)
                    name = doc.doc_name if doc else doc_id
                    await helper.delete_document(doc_id)
                    ok_list.append({"doc_id": doc_id, "doc_name": name})
                except Exception as de:
                    logger.error(f"[kb_manager] batch_delete single failed: {de}", exc_info=True)
                    fail_list.append({"doc_id": doc_id, "error": str(de)})

            try:
                await helper.refresh_kb()
            except Exception:
                pass

            if not ok_list and fail_list:
                return _error(
                    f"全部删除失败：{fail_list[0].get('error', '未知错误')}"
                )
            message = f"删除成功 {len(ok_list)} 个"
            if fail_list:
                message += f"，失败 {len(fail_list)} 个"
            return _ok(
                {
                    "message": message,
                    "ok": ok_list,
                    "fail": fail_list,
                    "success_count": len(ok_list),
                    "fail_count": len(fail_list),
                }
            )
        except Exception as e:
            logger.error(f"[kb_manager] batch_delete_document failed: {e}", exc_info=True)
            return _error(str(e))
