"""Typed tools for generally available Drive, Docs, Sheets, Slides, Chat and People APIs."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
from uuid import UUID

from app.agents.google_apis.client import GoogleApiClient, clamp, trim_text
from app.agents.mcp import McpToolInfo, _tool_prefix
from app.db.session import get_db_context
from app.repositories import chat_file as chat_file_repo
from app.services.file_storage import classify_file, get_file_storage

DRIVE_URL = "https://www.googleapis.com/drive/v3"
DOCS_URL = "https://docs.googleapis.com/v1"
SHEETS_URL = "https://sheets.googleapis.com/v4"
SLIDES_URL = "https://slides.googleapis.com/v1"
CHAT_URL = "https://chat.googleapis.com/v1"
PEOPLE_URL = "https://people.googleapis.com/v1"

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


@dataclass(frozen=True)
class Product:
    kind: str
    url: str
    scopes: tuple[str, ...]
    tools: tuple[tuple[str, str], ...]


DIRECT_GOOGLE_PRODUCTS: dict[str, Product] = {}


def _product(
    kind: str, url: str, scopes: tuple[str, ...], tools: tuple[tuple[str, str], ...]
) -> None:
    DIRECT_GOOGLE_PRODUCTS[kind] = Product(kind, url, scopes, tools)


_product(
    "drive",
    DRIVE_URL,
    (DRIVE_SCOPE,),
    (
        ("search_files", "Search files and folders in Google Drive."),
        ("list_recent_files", "List recently modified Drive files."),
        ("list_folder_contents", "List the direct children of a Drive folder."),
        ("get_file_metadata", "Get metadata for one Drive file."),
        ("read_file_content", "Read or export a Drive file as bounded text."),
        ("download_file_content", "Download a Drive file into Full Stack file storage."),
        ("list_permissions", "List permissions on a Drive file."),
        ("get_file_permissions", "List permissions on a Drive file (legacy tool name)."),
        ("create_folder", "Create a Drive folder."),
        ("upload_file", "Upload an attached Full Stack file to Drive."),
        ("copy_file", "Copy a Drive file."),
        ("update_file", "Rename, move, or change the trashed state of a Drive file."),
        ("move_file", "Move a Drive file between folders."),
        ("trash_file", "Move a Drive file to trash."),
        ("restore_file", "Restore a Drive file from trash."),
        ("delete_file", "Permanently delete a Drive file."),
        ("create_permission", "Share a Drive file with a user or group."),
        ("update_permission", "Change a Drive permission role."),
        ("delete_permission", "Remove a Drive permission."),
    ),
)
_product(
    "docs",
    DOCS_URL,
    ("https://www.googleapis.com/auth/documents", DRIVE_SCOPE),
    (
        ("search_docs", "Search Google Docs by name or content metadata."),
        ("read_doc", "Read a Google Doc including all tab content."),
        ("create_document", "Create a Google Doc."),
        ("insert_text", "Insert text at a document index."),
        ("append_text", "Append text to the end of a document."),
        ("replace_text", "Replace matching text throughout a document."),
        ("delete_text", "Delete a document text range."),
        ("format_text", "Apply bold, italic, underline, or font size to a range."),
        ("format_paragraph", "Apply alignment or named paragraph style to a range."),
        ("delete_document", "Permanently delete a Google Doc through Drive."),
    ),
)
_product(
    "sheets",
    SHEETS_URL,
    ("https://www.googleapis.com/auth/spreadsheets", DRIVE_SCOPE),
    (
        ("search_spreadsheets", "Search Google Sheets files."),
        ("get_spreadsheet", "Get spreadsheet structure and properties."),
        ("get_values", "Read values from an A1 range."),
        ("batch_get_values", "Read multiple A1 ranges."),
        ("create_spreadsheet", "Create a spreadsheet."),
        ("update_values", "Write values to an A1 range."),
        ("append_values", "Append rows to an A1 range."),
        ("clear_values", "Clear values from an A1 range."),
        ("add_sheet", "Add a sheet tab."),
        ("rename_sheet", "Rename a sheet tab."),
        ("delete_sheet", "Delete a sheet tab."),
        ("format_range", "Apply basic text formatting to a grid range."),
        ("delete_spreadsheet", "Permanently delete a spreadsheet through Drive."),
    ),
)
_product(
    "slides",
    SLIDES_URL,
    ("https://www.googleapis.com/auth/presentations", DRIVE_SCOPE),
    (
        ("search_presentations", "Search Google Slides presentations."),
        ("read_presentation", "Read a presentation and its page elements."),
        ("get_page", "Read one presentation page."),
        ("get_thumbnail", "Get a temporary thumbnail URL for a page."),
        ("create_presentation", "Create a presentation."),
        ("add_slide", "Add a slide using a predefined layout."),
        ("duplicate_slide", "Duplicate an existing slide."),
        ("move_slides", "Move slides to a new position."),
        ("delete_slide", "Delete a slide."),
        ("create_text_box", "Create a text box on a slide."),
        ("create_image", "Create an image on a slide from an HTTPS URL."),
        ("insert_text", "Insert text into a page element."),
        ("replace_text", "Replace matching text throughout a presentation."),
        ("format_text", "Apply basic text formatting to a page element range."),
        ("delete_presentation", "Permanently delete a presentation through Drive."),
    ),
)
_product(
    "chat",
    CHAT_URL,
    (
        "https://www.googleapis.com/auth/chat.spaces",
        "https://www.googleapis.com/auth/chat.messages",
        "https://www.googleapis.com/auth/chat.memberships",
        "https://www.googleapis.com/auth/chat.messages.reactions",
        "https://www.googleapis.com/auth/chat.delete",
    ),
    (
        ("list_spaces", "List Chat spaces the user belongs to."),
        ("get_space", "Get one Chat space."),
        ("create_space", "Create a Chat space."),
        ("update_space", "Update a Chat space name or description."),
        ("delete_space", "Permanently delete a Chat space."),
        ("list_messages", "List messages in a Chat space."),
        ("search_messages", "Search messages locally across generally available list results."),
        ("get_message", "Get one Chat message."),
        ("create_message", "Send a text message to a Chat space."),
        ("update_message", "Update a Chat message."),
        ("delete_message", "Delete a Chat message."),
        ("list_reactions", "List reactions on a message."),
        ("create_reaction", "Add an emoji reaction to a message."),
        ("delete_reaction", "Delete a reaction."),
        ("list_memberships", "List members of a Chat space."),
        ("create_membership", "Add a user to a Chat space."),
        ("delete_membership", "Remove a membership from a Chat space."),
    ),
)
_product(
    "contacts",
    PEOPLE_URL,
    (
        "https://www.googleapis.com/auth/contacts",
        "https://www.googleapis.com/auth/contacts.other.readonly",
        "https://www.googleapis.com/auth/directory.readonly",
        "https://www.googleapis.com/auth/userinfo.profile",
    ),
    (
        ("get_user_profile", "Get the authenticated user's Google profile."),
        ("list_contacts", "List the authenticated user's contacts."),
        ("get_contact", "Get one contact."),
        ("search_contacts", "Search contacts by name, email, phone, or organization."),
        ("list_other_contacts", "List automatically saved other contacts."),
        ("search_directory_people", "Search a managed Workspace domain directory."),
        ("create_contact", "Create a contact."),
        ("update_contact", "Update a contact using its etag."),
        ("delete_contact", "Delete a contact."),
        ("list_contact_groups", "List contact groups."),
        ("create_contact_group", "Create a contact group."),
        ("update_contact_group", "Rename a contact group."),
        ("delete_contact_group", "Delete a contact group."),
        ("modify_contact_group", "Add or remove contacts from a group."),
    ),
)


def _q(value: str) -> str:
    return quote(value, safe="")


async def _drive_search(client: GoogleApiClient, query: str, page_size: int = 20) -> dict[str, Any]:
    return await client.request(
        "GET",
        f"{DRIVE_URL}/files",
        params={
            "q": query,
            "pageSize": clamp(page_size, 1, 100),
            "orderBy": "modifiedTime desc",
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,createdTime,parents,size,webViewLink,trashed)",
        },
    )


async def probe_product(kind: str, access_token: str) -> list[McpToolInfo]:
    product = DIRECT_GOOGLE_PRODUCTS[kind]
    client = GoogleApiClient(access_token)
    if kind in {"drive", "docs", "sheets", "slides"}:
        mime = {
            "docs": "application/vnd.google-apps.document",
            "sheets": "application/vnd.google-apps.spreadsheet",
            "slides": "application/vnd.google-apps.presentation",
        }.get(kind)
        query = "trashed = false" + (f" and mimeType = '{mime}'" if mime else "")
        await _drive_search(client, query, 1)
    elif kind == "chat":
        await client.request("GET", f"{CHAT_URL}/spaces", params={"pageSize": 1})
    else:
        await client.request(
            "GET",
            f"{PEOPLE_URL}/people/me",
            params={"personFields": "names,emailAddresses"},
        )
    return [McpToolInfo(name=name, description=description) for name, description in product.tools]


async def _load_user_file(user_id: str, file_id: str) -> tuple[str, str, bytes]:
    async with get_db_context() as db:
        record = await chat_file_repo.get_by_id(db, UUID(file_id))
        if record is None or str(record.user_id) != str(user_id):
            raise ValueError("Attached file not found")
        data = await get_file_storage().load(record.storage_path)
        return record.filename, record.mime_type, data


async def _store_user_file(
    user_id: str, filename: str, mime_type: str, data: bytes
) -> dict[str, Any]:
    storage = get_file_storage()
    path = await storage.save(user_id, filename, data)
    async with get_db_context() as db:
        record = await chat_file_repo.create(
            db,
            user_id=UUID(user_id),
            filename=filename,
            mime_type=mime_type,
            size=len(data),
            storage_path=path,
            file_type=classify_file(mime_type, filename),
        )
    return {
        "file_id": str(record.id),
        "filename": filename,
        "mime_type": mime_type,
        "size": len(data),
    }


def build_product_toolset(
    *, kind: str, name: str, access_token: str, allowed_tools: list[str] | None, user_id: str | None
) -> Any:
    from pydantic_ai.toolsets import FunctionToolset

    product = next(p for p in DIRECT_GOOGLE_PRODUCTS.values() if p.kind == kind)
    descriptions = dict(product.tools)
    client = GoogleApiClient(access_token)
    toolset: Any = FunctionToolset(id=f"google-api:{name}")

    def add(func: Callable[..., Any], tool_name: str, *, mutation: bool = False) -> None:
        if allowed_tools is None or tool_name in allowed_tools:
            toolset.add_function(
                func,
                name=tool_name,
                description=descriptions[tool_name],
                requires_approval=mutation,
            )

    if kind == "drive":

        async def search_files(
            query: str = "trashed = false", page_size: int = 20
        ) -> dict[str, Any]:
            return await _drive_search(client, query, page_size)

        async def list_recent_files(page_size: int = 20) -> dict[str, Any]:
            return await _drive_search(client, "trashed = false", page_size)

        async def list_folder_contents(folder_id: str, page_size: int = 100) -> dict[str, Any]:
            return await _drive_search(
                client, f"'{folder_id}' in parents and trashed = false", page_size
            )

        async def get_file_metadata(file_id: str) -> dict[str, Any]:
            return await client.request(
                "GET", f"{DRIVE_URL}/files/{_q(file_id)}", params={"fields": "*"}
            )

        async def read_file_content(
            file_id: str, export_mime_type: str = "text/plain"
        ) -> dict[str, Any]:
            meta = await get_file_metadata(file_id)
            mime = str(meta.get("mimeType", ""))
            if mime.startswith("application/vnd.google-apps."):
                url = f"{DRIVE_URL}/files/{_q(file_id)}/export"
                params = {"mimeType": export_mime_type}
            else:
                url = f"{DRIVE_URL}/files/{_q(file_id)}"
                params = {"alt": "media"}
            data = await client.request("GET", url, params=params, expect_bytes=True)
            return {"metadata": meta, "content": trim_text(data.decode("utf-8", "replace"))}

        async def download_file_content(
            file_id: str, export_mime_type: str = "application/pdf"
        ) -> dict[str, Any]:
            if user_id is None:
                raise ValueError("Downloads require an authenticated Full Stack user")
            meta = await get_file_metadata(file_id)
            mime = str(meta.get("mimeType", ""))
            native = mime.startswith("application/vnd.google-apps.")
            url = (
                f"{DRIVE_URL}/files/{_q(file_id)}/export"
                if native
                else f"{DRIVE_URL}/files/{_q(file_id)}"
            )
            params = {"mimeType": export_mime_type} if native else {"alt": "media"}
            data = await client.request("GET", url, params=params, expect_bytes=True)
            filename = str(meta.get("name") or file_id) + (
                ".pdf" if native and export_mime_type == "application/pdf" else ""
            )
            return await _store_user_file(
                user_id, filename, export_mime_type if native else mime, data
            )

        async def list_permissions(file_id: str) -> dict[str, Any]:
            return await client.request(
                "GET",
                f"{DRIVE_URL}/files/{_q(file_id)}/permissions",
                params={"fields": "permissions(*)"},
            )

        async def get_file_permissions(file_id: str) -> dict[str, Any]:
            return await list_permissions(file_id)

        async def create_folder(name: str, parent_id: str | None = None) -> dict[str, Any]:
            body: dict[str, Any] = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
            if parent_id:
                body["parents"] = [parent_id]
            return await client.request(
                "POST", f"{DRIVE_URL}/files", json=body, params={"fields": "*"}
            )

        async def upload_file(
            file_id: str, name: str | None = None, parent_id: str | None = None
        ) -> dict[str, Any]:
            if user_id is None:
                raise ValueError("Uploads require an authenticated Full Stack user")
            filename, mime_type, data = await _load_user_file(user_id, file_id)
            metadata: dict[str, Any] = {"name": name or filename}
            if parent_id:
                metadata["parents"] = [parent_id]
            boundary = "fullstack-google-upload"
            payload = (
                (
                    f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(metadata)}\r\n"
                    f"--{boundary}\r\nContent-Type: {mime_type}\r\n\r\n"
                ).encode()
                + data
                + f"\r\n--{boundary}--".encode()
            )
            return await client.request(
                "POST",
                "https://www.googleapis.com/upload/drive/v3/files",
                params={"uploadType": "multipart", "fields": "*"},
                content=payload,
                headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            )

        async def copy_file(
            file_id: str, name: str | None = None, parent_id: str | None = None
        ) -> dict[str, Any]:
            body: dict[str, Any] = {}
            if name:
                body["name"] = name
            if parent_id:
                body["parents"] = [parent_id]
            return await client.request(
                "POST", f"{DRIVE_URL}/files/{_q(file_id)}/copy", json=body, params={"fields": "*"}
            )

        async def update_file(
            file_id: str,
            name: str | None = None,
            add_parent: str | None = None,
            remove_parent: str | None = None,
            trashed: bool | None = None,
        ) -> dict[str, Any]:
            body: dict[str, Any] = {}
            if name is not None:
                body["name"] = name
            if trashed is not None:
                body["trashed"] = trashed
            params: dict[str, Any] = {"fields": "*"}
            if add_parent:
                params["addParents"] = add_parent
            if remove_parent:
                params["removeParents"] = remove_parent
            return await client.request(
                "PATCH", f"{DRIVE_URL}/files/{_q(file_id)}", json=body, params=params
            )

        async def delete_file(file_id: str) -> dict[str, Any]:
            return await client.request("DELETE", f"{DRIVE_URL}/files/{_q(file_id)}")

        async def move_file(
            file_id: str, destination_folder_id: str, current_parent_id: str | None = None
        ) -> dict[str, Any]:
            return await update_file(
                file_id,
                add_parent=destination_folder_id,
                remove_parent=current_parent_id,
            )

        async def trash_file(file_id: str) -> dict[str, Any]:
            return await update_file(file_id, trashed=True)

        async def restore_file(file_id: str) -> dict[str, Any]:
            return await update_file(file_id, trashed=False)

        async def create_permission(
            file_id: str,
            email: str,
            role: str = "reader",
            permission_type: str = "user",
            send_notification: bool = True,
        ) -> dict[str, Any]:
            return await client.request(
                "POST",
                f"{DRIVE_URL}/files/{_q(file_id)}/permissions",
                params={"sendNotificationEmail": str(send_notification).lower()},
                json={"type": permission_type, "role": role, "emailAddress": email},
            )

        async def update_permission(file_id: str, permission_id: str, role: str) -> dict[str, Any]:
            return await client.request(
                "PATCH",
                f"{DRIVE_URL}/files/{_q(file_id)}/permissions/{_q(permission_id)}",
                json={"role": role},
            )

        async def delete_permission(file_id: str, permission_id: str) -> dict[str, Any]:
            return await client.request(
                "DELETE", f"{DRIVE_URL}/files/{_q(file_id)}/permissions/{_q(permission_id)}"
            )

        funcs = locals()
    elif kind == "docs":

        async def search_docs(query: str = "", page_size: int = 20) -> dict[str, Any]:
            q = "trashed = false and mimeType = 'application/vnd.google-apps.document'"
            if query:
                q += f" and name contains '{query.replace(chr(39), chr(92) + chr(39))}'"
            return await _drive_search(client, q, page_size)

        async def read_doc(document_id: str) -> dict[str, Any]:
            return await client.request(
                "GET",
                f"{DOCS_URL}/documents/{_q(document_id)}",
                params={"includeTabsContent": "true"},
            )

        async def create_document(title: str) -> dict[str, Any]:
            return await client.request("POST", f"{DOCS_URL}/documents", json={"title": title})

        async def _batch(
            document_id: str,
            requests: list[dict[str, Any]],
            required_revision_id: str | None = None,
        ) -> dict[str, Any]:
            body: dict[str, Any] = {"requests": requests}
            if required_revision_id:
                body["writeControl"] = {"requiredRevisionId": required_revision_id}
            return await client.request(
                "POST",
                f"{DOCS_URL}/documents/{_q(document_id)}:batchUpdate",
                json=body,
            )

        async def insert_text(
            document_id: str,
            text: str,
            index: int = 1,
            required_revision_id: str | None = None,
        ) -> dict[str, Any]:
            return await _batch(
                document_id,
                [{"insertText": {"location": {"index": max(1, index)}, "text": text}}],
                required_revision_id,
            )

        async def append_text(
            document_id: str, text: str, required_revision_id: str | None = None
        ) -> dict[str, Any]:
            return await _batch(
                document_id,
                [{"insertText": {"endOfSegmentLocation": {}, "text": text}}],
                required_revision_id,
            )

        async def replace_text(
            document_id: str,
            find: str,
            replace: str,
            match_case: bool = False,
            required_revision_id: str | None = None,
        ) -> dict[str, Any]:
            return await _batch(
                document_id,
                [
                    {
                        "replaceAllText": {
                            "containsText": {"text": find, "matchCase": match_case},
                            "replaceText": replace,
                        }
                    }
                ],
                required_revision_id,
            )

        async def delete_text(
            document_id: str,
            start_index: int,
            end_index: int,
            required_revision_id: str | None = None,
        ) -> dict[str, Any]:
            return await _batch(
                document_id,
                [
                    {
                        "deleteContentRange": {
                            "range": {"startIndex": start_index, "endIndex": end_index}
                        }
                    }
                ],
                required_revision_id,
            )

        async def format_text(
            document_id: str,
            start_index: int,
            end_index: int,
            bold: bool | None = None,
            italic: bool | None = None,
            underline: bool | None = None,
            font_size: float | None = None,
            required_revision_id: str | None = None,
        ) -> dict[str, Any]:
            style: dict[str, Any] = {}
            fields: list[str] = []
            for key, value in (("bold", bold), ("italic", italic), ("underline", underline)):
                if value is not None:
                    style[key] = value
                    fields.append(key)
            if font_size is not None:
                style["fontSize"] = {"magnitude": font_size, "unit": "PT"}
                fields.append("fontSize")
            return await _batch(
                document_id,
                [
                    {
                        "updateTextStyle": {
                            "range": {"startIndex": start_index, "endIndex": end_index},
                            "textStyle": style,
                            "fields": ",".join(fields),
                        }
                    }
                ],
                required_revision_id,
            )

        async def format_paragraph(
            document_id: str,
            start_index: int,
            end_index: int,
            alignment: str | None = None,
            named_style: str | None = None,
            required_revision_id: str | None = None,
        ) -> dict[str, Any]:
            style: dict[str, Any] = {}
            fields: list[str] = []
            if alignment is not None:
                style["alignment"] = alignment
                fields.append("alignment")
            if named_style is not None:
                style["namedStyleType"] = named_style
                fields.append("namedStyleType")
            return await _batch(
                document_id,
                [
                    {
                        "updateParagraphStyle": {
                            "range": {"startIndex": start_index, "endIndex": end_index},
                            "paragraphStyle": style,
                            "fields": ",".join(fields),
                        }
                    }
                ],
                required_revision_id,
            )

        async def delete_document(document_id: str) -> dict[str, Any]:
            return await client.request("DELETE", f"{DRIVE_URL}/files/{_q(document_id)}")

        funcs = locals()
    elif kind == "sheets":

        async def search_spreadsheets(query: str = "", page_size: int = 20) -> dict[str, Any]:
            q = "trashed = false and mimeType = 'application/vnd.google-apps.spreadsheet'"
            if query:
                q += f" and name contains '{query.replace(chr(39), chr(92) + chr(39))}'"
            return await _drive_search(client, q, page_size)

        async def get_spreadsheet(
            spreadsheet_id: str, include_grid_data: bool = False
        ) -> dict[str, Any]:
            return await client.request(
                "GET",
                f"{SHEETS_URL}/spreadsheets/{_q(spreadsheet_id)}",
                params={"includeGridData": str(include_grid_data).lower()},
            )

        async def get_values(spreadsheet_id: str, range_a1: str) -> dict[str, Any]:
            return await client.request(
                "GET", f"{SHEETS_URL}/spreadsheets/{_q(spreadsheet_id)}/values/{_q(range_a1)}"
            )

        async def batch_get_values(spreadsheet_id: str, ranges: list[str]) -> dict[str, Any]:
            return await client.request(
                "GET",
                f"{SHEETS_URL}/spreadsheets/{_q(spreadsheet_id)}/values:batchGet",
                params={"ranges": ranges},
            )

        async def create_spreadsheet(title: str) -> dict[str, Any]:
            return await client.request(
                "POST", f"{SHEETS_URL}/spreadsheets", json={"properties": {"title": title}}
            )

        async def update_values(
            spreadsheet_id: str,
            range_a1: str,
            values: list[list[Any]],
            value_input_option: str = "USER_ENTERED",
        ) -> dict[str, Any]:
            return await client.request(
                "PUT",
                f"{SHEETS_URL}/spreadsheets/{_q(spreadsheet_id)}/values/{_q(range_a1)}",
                params={"valueInputOption": value_input_option},
                json={"range": range_a1, "values": values},
            )

        async def append_values(
            spreadsheet_id: str,
            range_a1: str,
            values: list[list[Any]],
            value_input_option: str = "USER_ENTERED",
        ) -> dict[str, Any]:
            return await client.request(
                "POST",
                f"{SHEETS_URL}/spreadsheets/{_q(spreadsheet_id)}/values/{_q(range_a1)}:append",
                params={"valueInputOption": value_input_option, "insertDataOption": "INSERT_ROWS"},
                json={"values": values},
            )

        async def clear_values(spreadsheet_id: str, range_a1: str) -> dict[str, Any]:
            return await client.request(
                "POST",
                f"{SHEETS_URL}/spreadsheets/{_q(spreadsheet_id)}/values/{_q(range_a1)}:clear",
                json={},
            )

        async def _sheet_batch(spreadsheet_id: str, request: dict[str, Any]) -> dict[str, Any]:
            return await client.request(
                "POST",
                f"{SHEETS_URL}/spreadsheets/{_q(spreadsheet_id)}:batchUpdate",
                json={"requests": [request]},
            )

        async def add_sheet(
            spreadsheet_id: str, title: str, row_count: int = 1000, column_count: int = 26
        ) -> dict[str, Any]:
            return await _sheet_batch(
                spreadsheet_id,
                {
                    "addSheet": {
                        "properties": {
                            "title": title,
                            "gridProperties": {"rowCount": row_count, "columnCount": column_count},
                        }
                    }
                },
            )

        async def rename_sheet(spreadsheet_id: str, sheet_id: int, title: str) -> dict[str, Any]:
            return await _sheet_batch(
                spreadsheet_id,
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": sheet_id, "title": title},
                        "fields": "title",
                    }
                },
            )

        async def delete_sheet(spreadsheet_id: str, sheet_id: int) -> dict[str, Any]:
            return await _sheet_batch(spreadsheet_id, {"deleteSheet": {"sheetId": sheet_id}})

        async def format_range(
            spreadsheet_id: str,
            sheet_id: int,
            start_row: int,
            end_row: int,
            start_column: int,
            end_column: int,
            bold: bool | None = None,
            italic: bool | None = None,
        ) -> dict[str, Any]:
            text_format = {k: v for k, v in (("bold", bold), ("italic", italic)) if v is not None}
            return await _sheet_batch(
                spreadsheet_id,
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": start_row,
                            "endRowIndex": end_row,
                            "startColumnIndex": start_column,
                            "endColumnIndex": end_column,
                        },
                        "cell": {"userEnteredFormat": {"textFormat": text_format}},
                        "fields": "userEnteredFormat.textFormat",
                    }
                },
            )

        async def delete_spreadsheet(spreadsheet_id: str) -> dict[str, Any]:
            return await client.request("DELETE", f"{DRIVE_URL}/files/{_q(spreadsheet_id)}")

        funcs = locals()
    elif kind == "slides":

        async def search_presentations(query: str = "", page_size: int = 20) -> dict[str, Any]:
            q = "trashed = false and mimeType = 'application/vnd.google-apps.presentation'"
            if query:
                q += f" and name contains '{query.replace(chr(39), chr(92) + chr(39))}'"
            return await _drive_search(client, q, page_size)

        async def read_presentation(presentation_id: str) -> dict[str, Any]:
            return await client.request("GET", f"{SLIDES_URL}/presentations/{_q(presentation_id)}")

        async def get_page(presentation_id: str, page_object_id: str) -> dict[str, Any]:
            return await client.request(
                "GET",
                f"{SLIDES_URL}/presentations/{_q(presentation_id)}/pages/{_q(page_object_id)}",
            )

        async def get_thumbnail(presentation_id: str, page_object_id: str) -> dict[str, Any]:
            return await client.request(
                "GET",
                f"{SLIDES_URL}/presentations/{_q(presentation_id)}/pages/{_q(page_object_id)}/thumbnail",
            )

        async def create_presentation(title: str) -> dict[str, Any]:
            return await client.request(
                "POST", f"{SLIDES_URL}/presentations", json={"title": title}
            )

        async def _slides_batch(
            presentation_id: str,
            request: dict[str, Any],
            required_revision_id: str | None = None,
        ) -> dict[str, Any]:
            body: dict[str, Any] = {"requests": [request]}
            if required_revision_id:
                body["writeControl"] = {"requiredRevisionId": required_revision_id}
            return await client.request(
                "POST",
                f"{SLIDES_URL}/presentations/{_q(presentation_id)}:batchUpdate",
                json=body,
            )

        async def add_slide(
            presentation_id: str,
            layout: str = "BLANK",
            insertion_index: int | None = None,
            required_revision_id: str | None = None,
        ) -> dict[str, Any]:
            body: dict[str, Any] = {"slideLayoutReference": {"predefinedLayout": layout}}
            if insertion_index is not None:
                body["insertionIndex"] = insertion_index
            return await _slides_batch(presentation_id, {"createSlide": body}, required_revision_id)

        async def duplicate_slide(
            presentation_id: str,
            page_object_id: str,
            required_revision_id: str | None = None,
        ) -> dict[str, Any]:
            return await _slides_batch(
                presentation_id,
                {"duplicateObject": {"objectId": page_object_id}},
                required_revision_id,
            )

        async def move_slides(
            presentation_id: str,
            page_object_ids: list[str],
            insertion_index: int,
            required_revision_id: str | None = None,
        ) -> dict[str, Any]:
            return await _slides_batch(
                presentation_id,
                {
                    "updateSlidesPosition": {
                        "slideObjectIds": page_object_ids,
                        "insertionIndex": insertion_index,
                    }
                },
                required_revision_id,
            )

        async def delete_slide(
            presentation_id: str,
            page_object_id: str,
            required_revision_id: str | None = None,
        ) -> dict[str, Any]:
            return await _slides_batch(
                presentation_id,
                {"deleteObject": {"objectId": page_object_id}},
                required_revision_id,
            )

        async def create_text_box(
            presentation_id: str,
            page_object_id: str,
            x: float,
            y: float,
            width: float,
            height: float,
            required_revision_id: str | None = None,
        ) -> dict[str, Any]:
            return await _slides_batch(
                presentation_id,
                {
                    "createShape": {
                        "shapeType": "TEXT_BOX",
                        "elementProperties": {
                            "pageObjectId": page_object_id,
                            "size": {
                                "width": {"magnitude": width, "unit": "PT"},
                                "height": {"magnitude": height, "unit": "PT"},
                            },
                            "transform": {
                                "scaleX": 1,
                                "scaleY": 1,
                                "translateX": x,
                                "translateY": y,
                                "unit": "PT",
                            },
                        },
                    }
                },
                required_revision_id,
            )

        async def create_image(
            presentation_id: str,
            page_object_id: str,
            image_url: str,
            x: float,
            y: float,
            width: float,
            height: float,
            required_revision_id: str | None = None,
        ) -> dict[str, Any]:
            if not image_url.startswith("https://"):
                raise ValueError("Slides image URLs must use HTTPS")
            return await _slides_batch(
                presentation_id,
                {
                    "createImage": {
                        "url": image_url,
                        "elementProperties": {
                            "pageObjectId": page_object_id,
                            "size": {
                                "width": {"magnitude": width, "unit": "PT"},
                                "height": {"magnitude": height, "unit": "PT"},
                            },
                            "transform": {
                                "scaleX": 1,
                                "scaleY": 1,
                                "translateX": x,
                                "translateY": y,
                                "unit": "PT",
                            },
                        },
                    }
                },
                required_revision_id,
            )

        async def insert_text(
            presentation_id: str,
            object_id: str,
            text: str,
            insertion_index: int = 0,
            required_revision_id: str | None = None,
        ) -> dict[str, Any]:
            return await _slides_batch(
                presentation_id,
                {
                    "insertText": {
                        "objectId": object_id,
                        "insertionIndex": insertion_index,
                        "text": text,
                    }
                },
                required_revision_id,
            )

        async def replace_text(
            presentation_id: str,
            find: str,
            replace: str,
            match_case: bool = False,
            required_revision_id: str | None = None,
        ) -> dict[str, Any]:
            return await _slides_batch(
                presentation_id,
                {
                    "replaceAllText": {
                        "containsText": {"text": find, "matchCase": match_case},
                        "replaceText": replace,
                    }
                },
                required_revision_id,
            )

        async def format_text(
            presentation_id: str,
            object_id: str,
            start_index: int,
            end_index: int,
            bold: bool | None = None,
            italic: bool | None = None,
            font_size: float | None = None,
            required_revision_id: str | None = None,
        ) -> dict[str, Any]:
            style: dict[str, Any] = {}
            fields: list[str] = []
            if bold is not None:
                style["bold"] = bold
                fields.append("bold")
            if italic is not None:
                style["italic"] = italic
                fields.append("italic")
            if font_size is not None:
                style["fontSize"] = {"magnitude": font_size, "unit": "PT"}
                fields.append("fontSize")
            return await _slides_batch(
                presentation_id,
                {
                    "updateTextStyle": {
                        "objectId": object_id,
                        "textRange": {
                            "type": "FIXED_RANGE",
                            "startIndex": start_index,
                            "endIndex": end_index,
                        },
                        "style": style,
                        "fields": ",".join(fields),
                    }
                },
                required_revision_id,
            )

        async def delete_presentation(presentation_id: str) -> dict[str, Any]:
            return await client.request("DELETE", f"{DRIVE_URL}/files/{_q(presentation_id)}")

        funcs = locals()
    elif kind == "chat":

        async def list_spaces(page_size: int = 100) -> dict[str, Any]:
            return await client.request(
                "GET", f"{CHAT_URL}/spaces", params={"pageSize": clamp(page_size, 1, 1000)}
            )

        async def get_space(space_name: str) -> dict[str, Any]:
            return await client.request("GET", f"{CHAT_URL}/{space_name}")

        async def create_space(display_name: str, description: str | None = None) -> dict[str, Any]:
            body: dict[str, Any] = {"spaceType": "SPACE", "displayName": display_name}
            if description:
                body["spaceDetails"] = {"description": description}
            return await client.request("POST", f"{CHAT_URL}/spaces", json=body)

        async def update_space(
            space_name: str, display_name: str | None = None, description: str | None = None
        ) -> dict[str, Any]:
            body: dict[str, Any] = {}
            fields: list[str] = []
            if display_name is not None:
                body["displayName"] = display_name
                fields.append("displayName")
            if description is not None:
                body["spaceDetails"] = {"description": description}
                fields.append("spaceDetails.description")
            return await client.request(
                "PATCH",
                f"{CHAT_URL}/{space_name}",
                params={"updateMask": ",".join(fields)},
                json=body,
            )

        async def delete_space(space_name: str) -> dict[str, Any]:
            return await client.request("DELETE", f"{CHAT_URL}/{space_name}")

        async def list_messages(
            space_name: str, page_size: int = 100, filter_query: str | None = None
        ) -> dict[str, Any]:
            params: dict[str, Any] = {
                "pageSize": clamp(page_size, 1, 1000),
                "orderBy": "createTime DESC",
            }
            if filter_query:
                params["filter"] = filter_query
            return await client.request("GET", f"{CHAT_URL}/{space_name}/messages", params=params)

        async def search_messages(
            query: str,
            space_name: str | None = None,
            max_spaces: int = 20,
            max_messages_per_space: int = 100,
        ) -> dict[str, Any]:
            spaces = (
                [{"name": space_name}]
                if space_name
                else (await list_spaces(max_spaces)).get("spaces", [])[:max_spaces]
            )
            needle = query.casefold()
            matches: list[dict[str, Any]] = []
            for space in spaces:
                data = await list_messages(str(space["name"]), max_messages_per_space)
                for message in data.get("messages", []):
                    if needle in str(message.get("text", "")).casefold():
                        matches.append(message)
            return {"messages": matches[:100], "searched_spaces": len(spaces)}

        async def get_message(message_name: str) -> dict[str, Any]:
            return await client.request("GET", f"{CHAT_URL}/{message_name}")

        async def create_message(space_name: str, text: str) -> dict[str, Any]:
            return await client.request(
                "POST", f"{CHAT_URL}/{space_name}/messages", json={"text": text}
            )

        async def update_message(message_name: str, text: str) -> dict[str, Any]:
            return await client.request(
                "PATCH",
                f"{CHAT_URL}/{message_name}",
                params={"updateMask": "text"},
                json={"text": text},
            )

        async def delete_message(message_name: str, force: bool = False) -> dict[str, Any]:
            return await client.request(
                "DELETE", f"{CHAT_URL}/{message_name}", params={"force": str(force).lower()}
            )

        async def list_reactions(message_name: str) -> dict[str, Any]:
            return await client.request("GET", f"{CHAT_URL}/{message_name}/reactions")

        async def create_reaction(message_name: str, emoji_unicode: str) -> dict[str, Any]:
            return await client.request(
                "POST",
                f"{CHAT_URL}/{message_name}/reactions",
                json={"emoji": {"unicode": emoji_unicode}},
            )

        async def delete_reaction(reaction_name: str) -> dict[str, Any]:
            return await client.request("DELETE", f"{CHAT_URL}/{reaction_name}")

        async def list_memberships(space_name: str) -> dict[str, Any]:
            return await client.request("GET", f"{CHAT_URL}/{space_name}/members")

        async def create_membership(space_name: str, email: str) -> dict[str, Any]:
            return await client.request(
                "POST",
                f"{CHAT_URL}/{space_name}/members",
                json={"member": {"name": f"users/{email}", "type": "HUMAN"}},
            )

        async def delete_membership(membership_name: str) -> dict[str, Any]:
            return await client.request("DELETE", f"{CHAT_URL}/{membership_name}")

        funcs = locals()
    else:
        person_fields = "names,emailAddresses,phoneNumbers,organizations,addresses,birthdays,metadata,memberships"

        async def get_user_profile() -> dict[str, Any]:
            return await client.request(
                "GET", f"{PEOPLE_URL}/people/me", params={"personFields": person_fields}
            )

        async def list_contacts(page_size: int = 100) -> dict[str, Any]:
            return await client.request(
                "GET",
                f"{PEOPLE_URL}/people/me/connections",
                params={"pageSize": clamp(page_size, 1, 1000), "personFields": person_fields},
            )

        async def get_contact(resource_name: str) -> dict[str, Any]:
            return await client.request(
                "GET", f"{PEOPLE_URL}/{resource_name}", params={"personFields": person_fields}
            )

        async def search_contacts(query: str, page_size: int = 30) -> dict[str, Any]:
            await client.request(
                "GET",
                f"{PEOPLE_URL}/people:searchContacts",
                params={"query": "", "pageSize": 1, "readMask": person_fields},
            )
            return await client.request(
                "GET",
                f"{PEOPLE_URL}/people:searchContacts",
                params={
                    "query": query,
                    "pageSize": clamp(page_size, 1, 30),
                    "readMask": person_fields,
                },
            )

        async def list_other_contacts(page_size: int = 100) -> dict[str, Any]:
            return await client.request(
                "GET",
                f"{PEOPLE_URL}/otherContacts",
                params={
                    "pageSize": clamp(page_size, 1, 1000),
                    "readMask": "names,emailAddresses,phoneNumbers",
                },
            )

        async def search_directory_people(query: str, page_size: int = 100) -> dict[str, Any]:
            return await client.request(
                "GET",
                f"{PEOPLE_URL}/people:searchDirectoryPeople",
                params={
                    "query": query,
                    "pageSize": clamp(page_size, 1, 500),
                    "readMask": person_fields,
                    "sources": "DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE",
                },
            )

        def _person(
            name: str, emails: list[str] | None, phones: list[str] | None, organization: str | None
        ) -> dict[str, Any]:
            body: dict[str, Any] = {"names": [{"givenName": name}]}
            if emails:
                body["emailAddresses"] = [{"value": value} for value in emails]
            if phones:
                body["phoneNumbers"] = [{"value": value} for value in phones]
            if organization:
                body["organizations"] = [{"name": organization}]
            return body

        async def create_contact(
            name: str,
            emails: list[str] | None = None,
            phones: list[str] | None = None,
            organization: str | None = None,
        ) -> dict[str, Any]:
            return await client.request(
                "POST",
                f"{PEOPLE_URL}/people:createContact",
                params={"personFields": person_fields},
                json=_person(name, emails, phones, organization),
            )

        async def update_contact(
            resource_name: str,
            etag: str,
            name: str,
            emails: list[str] | None = None,
            phones: list[str] | None = None,
            organization: str | None = None,
        ) -> dict[str, Any]:
            body = _person(name, emails, phones, organization)
            body.update({"resourceName": resource_name, "etag": etag})
            return await client.request(
                "PATCH",
                f"{PEOPLE_URL}/{resource_name}:updateContact",
                params={
                    "updatePersonFields": "names,emailAddresses,phoneNumbers,organizations",
                    "personFields": person_fields,
                },
                json=body,
            )

        async def delete_contact(resource_name: str) -> dict[str, Any]:
            return await client.request("DELETE", f"{PEOPLE_URL}/{resource_name}:deleteContact")

        async def list_contact_groups(page_size: int = 100) -> dict[str, Any]:
            return await client.request(
                "GET", f"{PEOPLE_URL}/contactGroups", params={"pageSize": clamp(page_size, 1, 1000)}
            )

        async def create_contact_group(name: str) -> dict[str, Any]:
            return await client.request(
                "POST", f"{PEOPLE_URL}/contactGroups", json={"contactGroup": {"name": name}}
            )

        async def update_contact_group(resource_name: str, name: str) -> dict[str, Any]:
            return await client.request(
                "PUT",
                f"{PEOPLE_URL}/{resource_name}",
                params={"updateGroupFields": "name"},
                json={"resourceName": resource_name, "name": name},
            )

        async def delete_contact_group(
            resource_name: str, delete_contacts: bool = False
        ) -> dict[str, Any]:
            return await client.request(
                "DELETE",
                f"{PEOPLE_URL}/{resource_name}",
                params={"deleteContacts": str(delete_contacts).lower()},
            )

        async def modify_contact_group(
            resource_name: str,
            add_resource_names: list[str] | None = None,
            remove_resource_names: list[str] | None = None,
        ) -> dict[str, Any]:
            return await client.request(
                "POST",
                f"{PEOPLE_URL}/{resource_name}/members:modify",
                json={
                    "resourceNamesToAdd": add_resource_names or [],
                    "resourceNamesToRemove": remove_resource_names or [],
                },
            )

        funcs = locals()

    mutations = {
        tool
        for tool, _ in product.tools
        if tool.startswith(
            (
                "create_",
                "update_",
                "delete_",
                "upload_",
                "copy_",
                "move_",
                "add_",
                "append_",
                "insert_",
                "replace_",
                "clear_",
                "format_",
                "modify_",
                "rename_",
                "duplicate_",
                "trash_",
                "restore_",
            )
        )
    }
    for tool_name, _ in product.tools:
        add(funcs[tool_name], tool_name, mutation=tool_name in mutations)
    return toolset.prefixed(_tool_prefix(name))
