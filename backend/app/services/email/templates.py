"""Template loader — reads pre-rendered HTML/text from emails/compiled/."""

from pathlib import Path

from app.services.email.exceptions import EmailTemplateError

_BACKEND_DIR = Path(__file__).resolve().parents[3]  # backend/  (…/app/services/email/templates.py)
_REPO_ROOT = _BACKEND_DIR.parent

# Compiled emails are looked up in order. In a repository checkout (dev, CI, plain
# `uv sync`) they live at the project root next to `backend/`; in the Docker image the
# build context is `backend/`, so compose bind-mounts `./emails` to `/app/emails`, which
# resolves relative to `backend/` inside the container.
_DIST_DIRS: tuple[Path, ...] = (
    _REPO_ROOT / "emails" / "compiled",
    _BACKEND_DIR / "emails" / "compiled",
)


def _load_raw(key: str, ext: str) -> str:
    filename = f"{key}.{ext}"
    for dist_dir in _DIST_DIRS:
        path = dist_dir / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
    raise EmailTemplateError(
        message=f"Email template '{filename}' not found",
        details={"searched": [str(d / filename) for d in _DIST_DIRS]},
    )


def _render(template: str, context: dict) -> str:
    """Replace [[variable]] placeholders with context values."""
    for k, v in context.items():
        template = template.replace(f"[[{k}]]", str(v) if v is not None else "")
    return template


def render_email(key: str, context: dict) -> tuple[str, str, str]:
    """Return (subject, html, text) for the given template key and context."""
    html_raw = _load_raw(key, "html")
    text_raw = _load_raw(key, "txt")

    # Subject is stored in the first line of .txt as "Subject: ..."
    lines = text_raw.splitlines()
    subject_line = lines[0] if lines else ""
    subject_raw = (
        subject_line.removeprefix("Subject:").strip()
        if subject_line.startswith("Subject:")
        else key
    )
    text_body = "\n".join(lines[1:]).strip()

    subject = _render(subject_raw, context)
    html = _render(html_raw, context)
    text = _render(text_body, context)
    return subject, html, text
