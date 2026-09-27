"""
scripts/git/credentials.py
==========================
GitCredentialManager: local GitHub credential storage, PAT authentication,
connection testing, and remote repository creation.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .utils import scrub_tokens, _NO_WINDOW_FLAG
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.git.utils import scrub_tokens, _NO_WINDOW_FLAG
    except (ImportError, ModuleNotFoundError):
        from scripts.git.utils import scrub_tokens, _NO_WINDOW_FLAG

logger = logging.getLogger(__name__)


class GitCredentialManager:
    """
    Manages local GitHub credentials, PAT authentication, and connection testing.
    """

    @staticmethod
    def get_config_path(engine_root: Path | str | None = None) -> Path:
        if engine_root is None:
            engine_root = Path(__file__).resolve().parent.parent.parent
        return Path(engine_root) / "ai_data" / "git_config.json"

    @classmethod
    def load_config(cls, engine_root: Path | str | None = None) -> dict[str, Any]:
        """Load GitHub settings from ai_data/git_config.json with default fallback and env support."""
        path = cls.get_config_path(engine_root)
        defaults: dict[str, Any] = {
            "github_username": "",
            "github_token": os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", ""),
            "default_remote": "origin",
            "default_branch": "main",
            "auto_create_remote": True,
            "auto_generate_changelog": True,
        }
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                defaults.update(data)
                raw_tok = str(defaults.get("github_token", "")).strip()
                if not raw_tok or raw_tok == "YOUR_GITHUB_PERSONAL_ACCESS_TOKEN_HERE" or raw_tok.startswith("__REPLACE_"):
                    env_tok = os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")
                    defaults["github_token"] = env_tok if env_tok else ""
            except Exception as exc:
                logger.warning("Failed to parse %s: %s", path, exc)
        else:
            example_path = path.parent / "git_config.example.json"
            if example_path.exists():
                try:
                    data = json.loads(example_path.read_text(encoding="utf-8"))
                    defaults.update(data)
                except Exception:
                    pass
            env_tok = os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")
            if env_tok:
                defaults["github_token"] = env_tok
            elif defaults.get("github_token") == "YOUR_GITHUB_PERSONAL_ACCESS_TOKEN_HERE":
                defaults["github_token"] = ""

        return defaults

    @classmethod
    def save_config(cls, config_dict: dict[str, Any], engine_root: Path | str | None = None) -> dict[str, Any]:
        """Persist updated credentials and settings."""
        path = cls.get_config_path(engine_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        current = cls.load_config(engine_root)
        current.update(config_dict)
        path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        logger.info("Saved GitHub configuration to %s", path)
        return current

    @classmethod
    def mask_token(cls, token_or_cfg: str | dict[str, Any]) -> str | dict[str, Any]:
        """Mask token for display (e.g. ghp_****1234), or mask github_token in a config dict."""
        if isinstance(token_or_cfg, dict):
            masked_dict = dict(token_or_cfg)
            if "github_token" in masked_dict:
                masked_dict["github_token"] = cls.mask_token(str(masked_dict["github_token"]))
            return masked_dict

        token = str(token_or_cfg) if token_or_cfg is not None else ""
        if not token:
            return ""
        if len(token) <= 8:
            return "****"
        return f"{token[:4]}****{token[-4:]}"

    @classmethod
    def scrub_text(cls, text: str, token: str | None = None) -> str:
        """Remove any sensitive tokens or password occurrences from a string/url."""
        return scrub_tokens(text, token)

    @classmethod
    def get_authenticated_url(
        cls,
        repo_url: str,
        username: str | None = None,
        token: str | None = None,
        engine_root: Path | str | None = None,
    ) -> str:
        """
        Inject credentials into HTTPS repository URL for headless non-interactive git commands.
        e.g. https://github.com/owner/repo.git -> https://user:token@github.com/owner/repo.git
        If url is a local path, already authenticated, or SSH, returns as-is.
        """
        if not repo_url or not (repo_url.startswith("http://") or repo_url.startswith("https://")):
            return repo_url

        cfg = cls.load_config(engine_root)
        user = username or cfg.get("github_username") or ""
        tok = token or cfg.get("github_token") or ""

        if not tok:
            return repo_url

        # Check if already authenticated
        if "@" in repo_url.split("//", 1)[-1]:
            return repo_url

        parsed = urllib.parse.urlsplit(repo_url)
        auth_netloc = f"{user}:{tok}@{parsed.netloc}" if user else f"{tok}@{parsed.netloc}"
        auth_url = urllib.parse.urlunsplit((parsed.scheme, auth_netloc, parsed.path, parsed.query, parsed.fragment))
        return auth_url

    @classmethod
    def test_connection(
        cls,
        repo_url: str,
        username: str | None = None,
        token: str | None = None,
        engine_root: Path | str | None = None,
    ) -> dict[str, Any]:
        """
        Test remote repository connection using git ls-remote in headless non-interactive mode.
        """
        git_bin = shutil.which("git")
        if not git_bin:
            return {"success": False, "message": "Git executable not found in PATH."}

        if not repo_url or not repo_url.strip():
            return {"success": False, "message": "Repository URL is required to test connection."}

        clean_url = repo_url.strip()
        auth_url = cls.get_authenticated_url(clean_url, username=username, token=token, engine_root=engine_root)

        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = "echo"

        try:
            res = subprocess.run(
                [git_bin, "ls-remote", "--heads", auth_url],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=12,
                check=False,
                creationflags=_NO_WINDOW_FLAG,
                shell=False,
            )
            clean_stdout = cls.scrub_text(res.stdout, token)
            clean_stderr = cls.scrub_text(res.stderr, token)

            if res.returncode == 0:
                branches = []
                for line in clean_stdout.splitlines():
                    parts = line.strip().split("\t")
                    if len(parts) == 2 and parts[1].startswith("refs/heads/"):
                        branches.append(parts[1].replace("refs/heads/", ""))
                return {
                    "success": True,
                    "message": "Connection successful! Remote repository verified.",
                    "branches": branches,
                }
            else:
                err_msg = clean_stderr.strip() or clean_stdout.strip() or "Failed to connect to remote repository."
                return {
                    "success": False,
                    "message": f"Connection failed: {err_msg}",
                    "branches": [],
                }
        except subprocess.TimeoutExpired:
            return {"success": False, "message": "Connection timed out (12s). Check URL or network access."}
        except Exception as exc:
            return {"success": False, "message": f"Error testing connection: {cls.scrub_text(str(exc), token)}"}

    @classmethod
    def create_remote_repo(
        cls,
        repo_name: str,
        private: bool = False,
        username: str | None = None,
        token: str | None = None,
        engine_root: Path | str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a new remote repository on GitHub via the GitHub REST API.
        If the repository already exists (HTTP 422), links cleanly to the existing repository.
        """
        cfg = cls.load_config(engine_root)
        user = username or cfg.get("github_username") or ""
        tok = token or cfg.get("github_token") or ""

        if not tok:
            return {
                "success": False,
                "message": "Personal Access Token (PAT) is required to auto-create GitHub repositories. Configure it in GitHub Settings.",
            }

        repo_name = repo_name.strip()
        if not repo_name:
            return {"success": False, "message": "Repository name cannot be empty."}

        url = "https://api.github.com/user/repos"
        payload = {
            "name": repo_name,
            "private": private,
            "description": description or f"Repository managed by SPD Analysis Engine ({repo_name})",
            "auto_init": False,
        }
        data_bytes = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {tok}",
                "User-Agent": "SPD-Analysis-Engine",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp_body = resp.read().decode("utf-8", errors="replace")
                data = json.loads(resp_body) if resp_body else {}
                clone_url = data.get("clone_url") or (f"https://github.com/{user}/{repo_name}.git" if user else f"https://github.com/{repo_name}.git")
                html_url = data.get("html_url") or (f"https://github.com/{user}/{repo_name}" if user else f"https://github.com/{repo_name}")
                return {
                    "success": True,
                    "created": True,
                    "already_exists": False,
                    "message": f"Successfully created GitHub repository '{repo_name}'.",
                    "clone_url": clone_url,
                    "html_url": html_url,
                    "full_name": data.get("full_name") or (f"{user}/{repo_name}" if user else repo_name),
                }
        except urllib.error.HTTPError as http_err:
            raw_err_body = ""
            try:
                raw_err_body = http_err.read().decode("utf-8", errors="replace")
            except Exception:
                pass

            # Handle 422: Repository already exists on this account
            if http_err.code == 422:
                clone_url = f"https://github.com/{user}/{repo_name}.git" if user else f"https://github.com/{repo_name}.git"
                html_url = f"https://github.com/{user}/{repo_name}" if user else f"https://github.com/{repo_name}"
                return {
                    "success": True,
                    "created": False,
                    "already_exists": True,
                    "message": f"Repository '{repo_name}' already exists on GitHub account. Linked successfully.",
                    "clone_url": clone_url,
                    "html_url": html_url,
                    "full_name": f"{user}/{repo_name}" if user else repo_name,
                }
            elif http_err.code in (401, 403):
                msg = cls.scrub_text(raw_err_body, tok)
                return {
                    "success": False,
                    "message": f"GitHub authentication error ({http_err.code}): verify your Personal Access Token has 'repo' scope. {msg}".strip(),
                }
            else:
                msg = cls.scrub_text(raw_err_body or str(http_err), tok)
                return {
                    "success": False,
                    "message": f"GitHub API error ({http_err.code}): {msg}",
                }
        except Exception as exc:
            return {
                "success": False,
                "message": f"Failed to communicate with GitHub API: {cls.scrub_text(str(exc), tok)}",
            }
