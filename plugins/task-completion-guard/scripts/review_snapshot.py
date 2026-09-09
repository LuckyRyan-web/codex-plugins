"""Bounded, read-only worktree snapshots for an independent reviewer.

Explicit paths are files relative to ``cwd``. In a Git worktree the canonical
repository root is used and every dirty/untracked path is always added. No
directory walk or Git index mutation is performed. Optional artifacts live
outside that root, under ``files/``, with a manifest and a bounded text patch.
"""

import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile


MAX_FILES = 200
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_DIFF_BYTES = 8 * 1024 * 1024
MAX_GIT_OUTPUT_BYTES = 8 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 2
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_CODE_SUFFIXES = frozenset((
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".vue", ".svelte", ".go", ".rs", ".rb", ".php", ".java", ".kt",
    ".kts", ".scala", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs",
    ".swift", ".m", ".mm", ".sh", ".bash", ".zsh", ".sql", ".lua",
    ".r", ".ex", ".exs", ".erl", ".hs", ".clj", ".dart", ".tf",
    ".json", ".yaml", ".yml", ".toml", ".xml", ".graphql", ".gql",
))
_CRITICAL = re.compile(
    r"auth|security|payment|migrat|permission|credential|secret|password|"
    r"billing|crypto|(?:^|[/_.-])(?:acl|rbac|token|policy)(?:$|[/_.-])",
    re.IGNORECASE,
)
_DIGEST_FIELDS = (
    "version", "root", "is_git", "head", "capture_paths", "paths", "entries",
    "file_count", "changed_lines", "code_file_count", "critical",
)


class SnapshotError(RuntimeError):
    """The review scope cannot be captured or verified safely and completely."""


class SnapshotLimitError(SnapshotError):
    """The scope itself exceeds a capture limit.

    Preparation arguments cannot shrink it: dirty and untracked files are
    always in scope, so repeating the capture reaches the same limit. Callers
    use this to tell an unclearable environment apart from a bad request.
    """


def _git(cwd, args, allow_failure=False, output_limit=None):
    limit = MAX_GIT_OUTPUT_BYTES if output_limit is None else output_limit
    overrides = ["-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false"]
    if args and args[0] == "diff":
        # --no-ext-diff/--no-textconv do not disable clean/process filters.
        # Those are executable commands too, so override every configured
        # filter before Git compares raw worktree content with HEAD.
        code, configured, _ = _git(
            cwd, ["config", "--null", "--name-only", "--get-regexp", r"^filter\..*\.(clean|process|required)$"],
            allow_failure=True,
        )
        if code not in (0, 1):
            raise SnapshotError("Cannot inspect Git content filters")
        filters = {os.fsdecode(key).rsplit(".", 1)[0] for key in configured.split(b"\x00") if key}
        for name in sorted(filters):
            overrides.extend(["-c", name + ".clean=", "-c", name + ".process=", "-c", name + ".required=false"])
    env = os.environ.copy()
    # Environment overrides must not redirect the command to another worktree.
    for key in tuple(env):
        if key.startswith("GIT_"):
            del env[key]
    env.update({
        "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
    })
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(
                ["git", "-C", str(cwd), "--no-pager"] + overrides + list(args),
                stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, env=env,
            )
            try:
                process.wait(timeout=GIT_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                raise SnapshotError("Git command exceeded the 2 second time limit") from exc
            if stdout.tell() > limit or stderr.tell() > MAX_GIT_OUTPUT_BYTES:
                raise SnapshotLimitError("Git output exceeded the snapshot size limit")
            stdout.seek(0)
            stderr.seek(0)
            output, error = stdout.read(), stderr.read()
    except OSError as exc:
        raise SnapshotError("Cannot execute Git for the review snapshot") from exc
    if process.returncode and not allow_failure:
        raise SnapshotError("Git snapshot command failed: " + error.decode("utf-8", "replace").strip())
    return process.returncode, output, error


def _repository(cwd):
    code, output, error = _git(cwd, ["rev-parse", "--show-toplevel"], allow_failure=True)
    if code:
        if b"not a git repository" in error:
            return cwd, False, None
        raise SnapshotError("Cannot establish the Git worktree root")
    root = Path(os.fsdecode(output[:-1] if output.endswith(b"\n") else output)).resolve(strict=True)
    code, output, _ = _git(root, ["rev-parse", "--verify", "HEAD"], allow_failure=True)
    if not code:
        head = output.decode("ascii").strip()
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
            raise SnapshotError("Invalid Git HEAD")
        return root, True, head
    # An unborn branch is valid; a broken detached HEAD is not.
    code, branch, _ = _git(root, ["symbolic-ref", "-q", "HEAD"], allow_failure=True)
    if code:
        raise SnapshotError("Cannot resolve Git HEAD")
    code, _, _ = _git(root, ["show-ref", "--verify", "--quiet", os.fsdecode(branch).strip()], allow_failure=True)
    if code != 1:
        raise SnapshotError("Cannot resolve Git HEAD")
    return root, True, None


def _relative_path(value):
    try:
        value = os.fspath(value)
    except TypeError as exc:
        raise SnapshotError("Snapshot paths must be relative file paths") from exc
    if not isinstance(value, str) or not value or "\x00" in value:
        raise SnapshotError("Snapshot paths must be nonempty relative file paths")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise SnapshotError("Snapshot path escapes its root: " + repr(value))
    return path.as_posix()


def _explicit_paths(cwd, root, paths):
    if paths is None:
        return []
    if isinstance(paths, (str, bytes, os.PathLike)):
        raise SnapshotError("paths must be a sequence of relative file paths")
    try:
        prefix = cwd.relative_to(root)
        result = {_relative_path((prefix / _relative_path(path)).as_posix()) for path in paths}
    except (TypeError, ValueError) as exc:
        raise SnapshotError("Invalid explicit snapshot paths") from exc
    return sorted(result)


def _diff_args(head):
    return ["diff", "--no-ext-diff", "--no-textconv", "--no-renames", head or EMPTY_TREE]


def _scope(root, is_git, head, explicit):
    dirty, untracked = set(), set()
    if is_git:
        _, output, _ = _git(root, _diff_args(head) + ["--name-only", "--diff-filter=ACDMRTUXB", "-z", "--"])
        dirty = {_relative_path(os.fsdecode(path)) for path in output.split(b"\x00") if path}
        _, output, _ = _git(root, ["ls-files", "--others", "--exclude-standard", "-z", "--"])
        untracked = {_relative_path(os.fsdecode(path)) for path in output.split(b"\x00") if path}
        if explicit:
            # Explicit ignored files are still in scope and must contribute to
            # the change size; exclude-standard only controls implicit scope.
            _, output, _ = _git(root, ["ls-files", "--cached", "-z", "--"] + [":(literal)" + path for path in explicit])
            tracked_explicit = {_relative_path(os.fsdecode(path)) for path in output.split(b"\x00") if path}
            untracked.update(set(explicit) - tracked_explicit)
    scope = sorted(set(explicit) | dirty | untracked)
    if len(scope) > MAX_FILES:
        raise SnapshotLimitError("Snapshot exceeds the 200 file limit")
    if not is_git and not scope:
        raise SnapshotError("A non-Git snapshot requires explicit file paths")
    return scope, untracked


def _read_file(root, relative):
    """Open each component without following links, including during races."""
    descriptors = []
    try:
        descriptor = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(descriptor)
        parts = PurePosixPath(relative).parts
        for part in parts[:-1]:
            descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            descriptors.append(descriptor)
        parent = descriptor
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SnapshotError("Snapshot path is not a regular file: " + relative)
        if before.st_size > MAX_FILE_BYTES:
            raise SnapshotLimitError("Snapshot file exceeds the 2 MiB limit: " + relative)
        chunks, size = [], 0
        while True:
            chunk = os.read(descriptor, min(65536, MAX_FILE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise SnapshotLimitError("Snapshot file exceeds the 2 MiB limit: " + relative)
        after = os.fstat(descriptor)
        current = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if identity(before) != identity(after) or identity(after) != identity(current):
            raise SnapshotError("Snapshot file changed while being read: " + relative)
        data = b"".join(chunks)
        return {"type": "file", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "mode": stat.S_IMODE(after.st_mode)}, data
    except FileNotFoundError:
        return {"type": "missing", "sha256": None, "size": 0, "mode": None}, None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise SnapshotError("Snapshot path contains a symlink or non-directory component: " + relative) from exc
        raise SnapshotError("Cannot read snapshot path: " + relative) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _read_scope(root, scope):
    entries, contents, total = {}, {}, 0
    for path in scope:
        entry, data = _read_file(root, path)
        entries[path] = entry
        if data is not None:
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise SnapshotLimitError("Snapshot content exceeds the 8 MiB total limit")
            contents[path] = data
    return entries, contents


def _patch_and_lines(root, is_git, head, scope, untracked, contents):
    if not is_git:
        return b"", sum(len(data.splitlines()) for data in contents.values())
    if not scope:
        return b"", 0
    literal_paths = [":(literal)" + path for path in scope]
    _, numbers, _ = _git(root, _diff_args(head) + ["--numstat", "-z", "--"] + literal_paths)
    changed_lines = 0
    for record in numbers.split(b"\x00"):
        if not record:
            continue
        fields = record.split(b"\t", 2)
        if len(fields) != 3:
            raise SnapshotError("Malformed Git numstat output")
        if fields[:2] == [b"-", b"-"]:
            continue  # Binary bytes are bounded and hashed, never expanded in a patch.
        try:
            changed_lines += int(fields[0]) + int(fields[1])
        except ValueError as exc:
            raise SnapshotError("Malformed Git numstat counts") from exc
    changed_lines += sum(len(contents[path].splitlines()) for path in untracked if path in contents)
    # Git's default patch reports binary differences without embedding blobs.
    _, patch, _ = _git(root, _diff_args(head) + ["--no-color", "--patch", "--"] + literal_paths, output_limit=MAX_DIFF_BYTES)
    return patch, changed_lines


def _digest(snapshot):
    payload = {key: snapshot[key] for key in _DIGEST_FIELDS}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _destination(root, destination):
    raw = Path(destination).expanduser().absolute()
    for part in [raw] + list(raw.parents):
        if part.is_symlink():
            raise SnapshotError("Snapshot destination must not contain symlinks")
    target = raw.resolve()
    if target == root or root in target.parents:
        raise SnapshotError("Snapshot destination must be outside the reviewed root")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise SnapshotError("Snapshot destination must be absent or an empty directory")
    return target


def _save(snapshot, contents, patch, destination):
    try:
        destination.mkdir(parents=True, exist_ok=True)
        files = destination / "files"
        files.mkdir()
        for relative, data in contents.items():
            target = files / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
            target.chmod(0o400)
        with (destination / "diff.patch").open("xb") as stream:
            stream.write(patch)
        with (destination / "entries.json").open("x", encoding="utf-8") as stream:
            json.dump(snapshot, stream, ensure_ascii=True, sort_keys=True, indent=2)
            stream.write("\n")
        (destination / "diff.patch").chmod(0o400)
        (destination / "entries.json").chmod(0o400)
        for directory, _, _ in os.walk(str(destination), topdown=False):
            Path(directory).chmod(0o500)
    except OSError as exc:
        raise SnapshotError("Cannot persist the independent review snapshot") from exc


def capture_snapshot(cwd, paths=None, destination=None):
    """Capture content plus scope identity; fail rather than truncate any limit.

    ``paths`` names individual files (including deleted files), relative to cwd.
    ``file_count`` includes deletions. ``capture_paths`` records the normalized
    explicit scope relative to the returned root, allowing faithful rechecks.
    Directory paths, symlinks, and special files are rejected. The destination,
    when provided, must be empty or absent and outside the reviewed root.
    """
    try:
        cwd = Path(cwd).expanduser().resolve(strict=True)
        if not cwd.is_dir():
            raise SnapshotError("Snapshot cwd must be a directory")
        root, is_git, head = _repository(cwd)
        explicit = _explicit_paths(cwd, root, paths)
        target = _destination(root, destination) if destination is not None else None
        scope, untracked = _scope(root, is_git, head, explicit)
        entries, contents = _read_scope(root, scope)
        patch, changed_lines = _patch_and_lines(root, is_git, head, scope, untracked, contents)
        # Ensure the patch, file copies, HEAD and scope describe one stable state.
        end_root, end_git, end_head = _repository(root)
        end_scope, end_untracked = _scope(root, end_git, end_head, explicit)
        end_entries, _ = _read_scope(root, end_scope)
        if (root, is_git, head, scope, untracked, entries) != (end_root, end_git, end_head, end_scope, end_untracked, end_entries):
            raise SnapshotError("Worktree changed while capturing the review snapshot")
        result = {
            "version": 1, "root": str(root), "is_git": is_git, "head": head,
            "capture_paths": explicit, "paths": scope, "entries": entries,
            "file_count": len(scope), "changed_lines": changed_lines,
            "code_file_count": sum(PurePosixPath(path).suffix.lower() in _CODE_SUFFIXES or PurePosixPath(path).name.lower() in ("dockerfile", "makefile") for path in scope),
            "critical": any(_CRITICAL.search(path) is not None for path in scope),
        }
        result["digest"] = _digest(result)
        if target is not None:
            result["snapshot_dir"] = str(target)
            _save(result, contents, patch, target)
        return result
    except SnapshotError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise SnapshotError("Cannot capture the review snapshot") from exc


def check_snapshot(snapshot):
    """Return False on changed scope/content/HEAD; raise on unsafe verification."""
    try:
        if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
            raise SnapshotError("Unsupported review snapshot")
        if _digest(snapshot) != snapshot["digest"]:
            raise SnapshotError("Review snapshot metadata is inconsistent")
        current = capture_snapshot(snapshot["root"], paths=snapshot["capture_paths"])
        return current["digest"] == snapshot["digest"]
    except SnapshotError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise SnapshotError("Invalid review snapshot metadata") from exc
