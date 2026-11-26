"""``cuflynx://open`` links -- open a COMBINE archive or a study directory from a page.

Two links, one action:

* ``cuflynx://open?url=<percent-encoded archive URL>`` -- an ``https:``, ``http:``
  or ``file:`` URL of a ``.omex``. CUFLynx fetches the archive; accepting it runs the
  same importer a dropped ``.omex`` does.
* ``cuflynx://open?study=<percent-encoded file: URL>`` -- a study on this machine:
  its ``*_cuflynx_study.json`` (or bare ``cuflynx_study.json``), or the directory
  holding it. Accepting it opens that directory as the outputs directory, as dropping
  the manifest does (``POST /api/outputs/manifest``), so the study's sensitivity
  analyses, emulator and runs load -- the parts an archive cannot carry.

A link carries exactly one of ``url`` and ``study``. The operating system starts
CUFLynx with it (see ``apps/desktop/url_scheme.py`` for the per-platform
registration), and CUFLynx **stages** what it names in the inbox, as a study posted
by PhLynx is staged: the UI asks before loading it, naming where it came from (and,
for a study, the manifest's path).

Staged rather than loaded because any page can launch a ``cuflynx://`` link;
the inbox's confirmation is the security control for every delivery.

This module owns:

* :func:`parse` -- the link grammar: the ``open`` action and one ``url`` or ``study``;
* :func:`fetch` -- the archive download, capped at :data:`inbox.MAX_DELIVERY_BYTES`;
* :func:`stage` -- parse, read, check, stage; a failure is recorded with
  :meth:`inbox.Inbox.report_error` so the UI shows it;
* the single-instance handoff: a running app writes :func:`instance_file`
  (its port and a random token), and a second launch with a link posts the link
  to that app's ``/api/open-link`` (:func:`hand_off`) and exits.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import omex_import
import study_manifest
from inbox import MAX_DELIVERY_BYTES, inbox as default_inbox

SCHEME = "cuflynx"
ACTIONS = ("open",)
URL_SCHEMES = ("https", "http", "file")
#: The query keys a link names its target with: an archive, or a study on this machine.
TARGETS = ("url", "study")

#: Longer links than this are refused before they are parsed. A link carries a
#: URL, not an archive, so real ones are a few hundred characters.
MAX_LINK_LENGTH = 8192

#: Seconds without a byte before a download is abandoned.
DOWNLOAD_TIMEOUT = 60

#: The header a handoff carries the running instance's token in.
TOKEN_HEADER = "X-CUFLynx-Token"

INSTANCE_FILENAME = "instance.json"

_token: str | None = None


class OpenLinkError(ValueError):
    """A link that cannot be opened, in words the UI can show."""


@dataclass(frozen=True)
class Link:
    """What a link opens: ``kind`` is ``"archive"`` (``url=``) or ``"study"`` (``study=``)."""

    kind: str
    url: str


def parse_link(link: str) -> str:
    """The URL a ``cuflynx://open`` link names, archive or study. See :func:`parse`."""
    return parse(link).url


def parse(link: str) -> Link:
    """What a ``cuflynx://open?url=...`` or ``cuflynx://open?study=...`` link opens.

    Raises :class:`OpenLinkError` for any other action, for a link with neither or both
    of ``url`` and ``study`` or either repeated, for an archive URL that is not
    ``https:``, ``http:`` or ``file:``, and for a study URL that is not a ``file:`` URL
    on this computer.
    """
    link = str(link or "").strip()
    if not link:
        raise OpenLinkError("the link is empty")
    if len(link) > MAX_LINK_LENGTH:
        raise OpenLinkError(f"the link is longer than {MAX_LINK_LENGTH} characters")
    parts = urllib.parse.urlsplit(link)
    if parts.scheme.lower() != SCHEME:
        raise OpenLinkError(f"not a {SCHEME}:// link: {link[:200]}")
    # `cuflynx://open?...` puts the action in the host; `cuflynx:open?...` in the
    # path. A trailing slash (`cuflynx://open/?...`) is added by some launchers.
    host, path = parts.netloc, parts.path.strip("/")
    if host and path:
        raise OpenLinkError(f"unknown {SCHEME}:// action {host + '/' + path!r}")
    action = (host or path).lower()
    if action not in ACTIONS:
        raise OpenLinkError(
            f"unknown {SCHEME}:// action {action!r}; CUFLynx understands "
            + ", ".join(repr(a) for a in ACTIONS)
        )
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    given = [(key, value) for key in TARGETS for value in query.get(key, [])]
    if len(given) != 1 or not given[0][1].strip():
        raise OpenLinkError(
            f"a {SCHEME}://open link needs exactly one of url=<archive URL> "
            "or study=<file: URL of a study manifest or directory>")
    key, value = given[0]
    if key == "study":
        return Link("study", validate_study_url(value.strip()))
    return Link("archive", validate_archive_url(value.strip()))


def validate_archive_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in URL_SCHEMES:
        raise OpenLinkError(
            f"the archive URL must be https:, http: or file:, not {scheme + ':' if scheme else url[:200]!r}"
        )
    if scheme in ("http", "https") and not parts.hostname:
        raise OpenLinkError(f"the archive URL names no host: {url[:200]}")
    if scheme == "file":
        if parts.netloc not in ("", "localhost"):
            raise OpenLinkError(f"a file: URL must be on this computer, not {parts.netloc!r}")
        if not parts.path:
            raise OpenLinkError("the file: URL names no file")
    return url


def validate_study_url(url: str) -> str:
    """A study link's URL: a ``file:`` URL on this computer, since a study is a directory."""
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme != "file":
        raise OpenLinkError(
            "a study link must name a study on this computer with a file: URL, not "
            f"{scheme + ':' if scheme else url[:200]!r}. A study directory cannot be "
            "downloaded; link to its .omex archive with url= instead.")
    if parts.netloc not in ("", "localhost"):
        raise OpenLinkError(f"a file: URL must be on this computer, not {parts.netloc!r}")
    if not parts.path:
        raise OpenLinkError("the file: URL names no study")
    return url


def origin_of(url: str) -> str:
    """Where an archive came from, as the inbox dialog names it."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() == "file":
        return "A file on this computer"
    return f"{parts.scheme.lower()}://{parts.netloc}"


def filename_of(url: str) -> str:
    """The archive's filename for the dialog: the URL's last path segment."""
    name = Path(urllib.parse.unquote(urllib.parse.urlsplit(url).path)).name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem).strip("._-") or "study"
    return stem + ".omex"


def file_path_of(url: str) -> Path:
    return Path(urllib.request.url2pathname(urllib.parse.urlsplit(url).path))


def fetch(url: str, max_bytes: int = MAX_DELIVERY_BYTES, timeout: float = DOWNLOAD_TIMEOUT,
          opener=urllib.request.urlopen) -> bytes:
    """The archive's bytes, or :class:`OpenLinkError`. Never more than ``max_bytes``."""
    limit = f"the {max_bytes // (1024 * 1024)} MB limit" if max_bytes >= 1024 * 1024 else f"{max_bytes} bytes"
    if urllib.parse.urlsplit(url).scheme.lower() == "file":
        path = file_path_of(url)
        try:
            size = path.stat().st_size
            if size > max_bytes:
                raise OpenLinkError(f"{path} is larger than {limit}")
            return path.read_bytes()
        except OSError as exc:
            raise OpenLinkError(f"could not read {path}: {exc.strerror or exc}") from exc

    request = urllib.request.Request(url, headers={"User-Agent": "CUFLynx"})
    try:
        with opener(request, timeout=timeout) as resp:
            # urllib follows redirects only to http(s), so the final URL is a web one.
            declared = resp.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise OpenLinkError(f"the archive is larger than {limit}")
            chunks, total = [], 0
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise OpenLinkError(f"the archive is larger than {limit}")
                chunks.append(chunk)
            return b"".join(chunks)
    except OpenLinkError:
        raise
    except urllib.error.HTTPError as exc:
        raise OpenLinkError(f"the server answered {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise OpenLinkError(f"could not download it: {exc.reason}") from exc
    except (OSError, ValueError) as exc:
        raise OpenLinkError(f"could not download it: {exc}") from exc


def read_study(url: str, max_bytes: int = MAX_DELIVERY_BYTES) -> tuple[str, bytes, dict]:
    """The manifest a study URL names: ``(path, bytes as read, parsed manifest)``.

    A directory names the manifest in it (:func:`study_manifest.find`: the newest when
    there are several). Raises :class:`OpenLinkError` when there is no such file or
    directory, it holds no manifest, the file is not one, or the manifest cannot be
    believed or opened (:func:`study_manifest.study_location`).
    """
    target = file_path_of(url)
    if target.is_dir():
        found = study_manifest.find(str(target))
        if not found:
            raise OpenLinkError(
                f"{target} holds no {study_manifest.MANIFEST_NAME} or "
                f"*{study_manifest.MANIFEST_SUFFIX}")
        target = Path(found)
    elif not target.exists():
        raise OpenLinkError(f"{target} does not exist")
    elif not study_manifest.is_manifest_name(target.name):
        raise OpenLinkError(
            f"{target.name} is not a study manifest: a study link names a "
            f"*{study_manifest.MANIFEST_SUFFIX} or {study_manifest.MANIFEST_NAME}, or the "
            "directory holding one")
    try:
        if target.stat().st_size > max_bytes:
            raise OpenLinkError(f"{target} is larger than {max_bytes} bytes")
        data = target.read_bytes()
    except OSError as exc:
        raise OpenLinkError(f"could not read {target}: {exc.strerror or exc}") from exc
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise OpenLinkError(f"{target} is not a study manifest: it is not JSON ({exc})") from exc
    try:
        study_manifest.study_location(raw, str(target), str(target))
    except study_manifest.ManifestError as exc:
        raise OpenLinkError(str(exc)) from exc
    return str(target), data, raw


def declared_parts(raw: dict) -> list[str]:
    """What a manifest says its study holds, one line each, for the dialog."""
    parts = [f"{key}: {raw[key]}" for key in study_manifest.PATH_KEYS if raw.get(key)]
    for section in ("sensitivity", "runs"):
        entries = [e for e in raw.get(section) or [] if isinstance(e, dict)]
        if entries:
            parts.append(f"{section}: {len(entries)} recorded")
    return parts


def stage(link: str, box=None, fetcher=fetch) -> dict:
    """Stage what a link names in the inbox: the fetched archive, or the study manifest.

    Returns the inbox summary, or ``{"error": message}`` after recording the
    failure for the UI. Never raises: it runs on a background thread.
    """
    box = box or default_inbox
    origin = "A cuflynx:// link"
    try:
        target = parse(link)
        if target.kind == "study":
            path, data, raw = read_study(target.url)
            return box.deliver_study(path, data, origin, declared_parts(raw))
        url = target.url
        origin = origin_of(url)
        data = fetcher(url)
        if not data:
            raise OpenLinkError("the archive is empty")
        try:
            omex_import.unpack(data)
        except omex_import.OmexImportError as exc:
            raise OpenLinkError(f"it is not a COMBINE archive CUFLynx can read: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - a background thread must report, not die
        message = f"Could not open the study from {_shown(link)}: {exc}"
        box.report_error(origin, message)
        return {"error": message}
    return box.deliver(data, origin, filename_of(url))


def _shown(link: str) -> str:
    """The archive or study URL when the link parses far enough, else the link; shortened."""
    try:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(str(link)).query)
        text = next((query[key][0] for key in TARGETS if query.get(key)), str(link))
    except ValueError:
        text = str(link)
    return text if len(text) <= 200 else text[:197] + "..."


# ---------------------------------------------------------------------------
# Single instance
# ---------------------------------------------------------------------------
def set_token(token: str | None) -> None:
    """The token ``/api/open-link`` requires. None (the default) disables the route."""
    global _token
    _token = token


def token_matches(given: str | None) -> bool:
    return bool(_token) and hmac.compare_digest(str(given or ""), _token)


def instance_file() -> Path:
    import settings_store  # noqa: PLC0415 - honours CUFLYNX_CONFIG_DIR at call time

    return settings_store.config_dir() / INSTANCE_FILENAME


def write_instance(port: int, token: str, path: Path | None = None) -> Path:
    """Record this app's port and handoff token for a later launch to find."""
    path = path or instance_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"port": int(port), "token": token, "pid": os.getpid()}, fh)
    os.replace(tmp, path)
    return path


def read_instance(path: Path | None = None) -> dict | None:
    path = path or instance_file()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("port"), int) or not doc.get("token"):
        return None
    return doc


def remove_instance(token: str, path: Path | None = None) -> None:
    """Delete the instance file if it is still this app's."""
    path = path or instance_file()
    doc = read_instance(path)
    if doc and doc.get("token") == token:
        try:
            path.unlink()
        except OSError:
            pass


def hand_off(link: str, path: Path | None = None, timeout: float = 5.0) -> bool:
    """Give ``link`` to an already-running CUFLynx. False when none answers.

    True also when the running app refused the link (422): it has shown the
    error in its window, which is where the user is looking.
    """
    doc = read_instance(path)
    if doc is None:
        return False
    request = urllib.request.Request(
        f"http://127.0.0.1:{doc['port']}/api/open-link",
        data=json.dumps({"link": link}).encode("utf-8"),
        headers={"Content-Type": "application/json", TOKEN_HEADER: str(doc["token"])},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        return exc.code == 422
    except (urllib.error.URLError, OSError, ValueError):
        return False
