"""Faux « claude » / « codex » / « agy » pour les tests : enregistre ce qu'on lui passe."""

import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
outil = os.environ.get("FAUX_OUTIL", "claude")
journal = os.environ.get("FAUX_JOURNAL")
flux = os.environ.get("FAUX_FLUX")  # messages intermédiaires (mini-rapports) avant la réponse


def noter(entree):
    if journal:
        with open(journal, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "outil": outil, "args": args, "entree": entree, "cwd": os.getcwd(),
                "env": {k: os.environ.get(k) for k in (
                    "CLAUDE_CONFIG_DIR", "CODEX_HOME", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "GEMINI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")},
            }, ensure_ascii=False) + "\n")


def ligne(objet):
    print(json.dumps(objet, ensure_ascii=False), flush=True)


if outil == "claude":
    if args[:2] == ["auth", "status"]:
        noter(None)
        print("Connecté avec un compte Claude (faux)")
        sys.exit(0)
    entree = sys.stdin.read()
    noter(entree)
    if os.environ.get("FAUX_ERREUR"):
        ligne({"type": "result", "subtype": "success", "is_error": True, "result": "Claude AI usage limit reached"})
        sys.exit(1)
    if "--allowedTools" in args and "Write" in args[args.index("--allowedTools") + 1]:
        Path("cree_par_claude.txt").write_text("bonjour", encoding="utf-8")
    ligne({"type": "system", "subtype": "init", "tools": []})
    if flux:
        ligne({"type": "assistant", "message": {"content": [{"type": "text", "text": "Je lis le brief."}]}})
        ligne({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]}})
        ligne({"type": "user", "message": {"content": [{"type": "tool_result", "content": "..."}]}})
    ligne({"type": "assistant", "message": {"content": [{"type": "text", "text": "Réponse de Claude é à ç"}]}})
    ligne({"type": "result", "subtype": "success", "is_error": False,
           "result": "Réponse de Claude é à ç", "session_id": "s1"})
    ligne({"type": "system", "subtype": "task_summary"})  # une ligne peut suivre le résultat

elif outil == "codex":
    if args[:2] == ["login", "status"]:
        noter(None)
        print("Logged in using ChatGPT")
        sys.exit(0)
    if args[:2] == ["debug", "models"]:
        noter(None)
        sys.stdout.write("﻿" + json.dumps({"models": [
            {"slug": "gpt-cache", "visibility": "hide", "priority": 1},
            {"slug": "gpt-6-luna", "display_name": "GPT-6-Luna", "visibility": "list", "priority": 5},
            {"slug": "gpt-5.6-terra", "display_name": "GPT-5.6-Terra", "visibility": "list", "priority": 8},
        ]}) + "\n")
        sys.exit(0)
    entree = sys.stdin.read()
    noter(entree)
    if os.environ.get("FAUX_ERREUR"):
        print("2026-11-01T08:29:14.128211Z  INFO codex_core::client: connexion…\n"
              "\x1b[31m2026-11-01T08:29:15.000000Z ERROR codex_core::exec: stream disconnected\x1b[0m", file=sys.stderr)
        ligne({"type": "thread.started", "thread_id": "t1"})
        ligne({"type": "error", "message": json.dumps({"detail": "The 'gpt-5.5' model is not supported when using Codex with a ChatGPT account."})})
        ligne({"type": "turn.failed", "error": {"message": json.dumps({"detail": "The 'gpt-5.5' model is not supported when using Codex with a ChatGPT account."})}})
        sys.exit(1)
    ligne({"type": "thread.started", "thread_id": "t1"})
    if flux:
        ligne({"type": "item.completed", "item": {"id": "i0", "type": "agent_message", "text": "Je commence par le plan."}})
        ligne({"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "dir"}})
        ligne({"type": "item.completed", "item": {"id": "i1", "type": "command_execution", "command": "dir"}})
    ligne({"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": "Réponse de Codex"}})
    sortie = args[args.index("-o") + 1]
    Path(sortie).write_text("Réponse de Codex", encoding="utf-8")
    print("journal de progression ignoré", file=sys.stderr)

elif outil == "agy":
    if args == ["models"]:
        noter(None)
        print("Available models:\n  gemini-3.1-pro      Gemini 3.1 Pro\n  gemini-3.8-flash    Flash\n")
        sys.exit(0)
    noter(sys.stdin.read() if not sys.stdin.isatty() else None)
    if os.environ.get("FAUX_ERREUR"):
        print('jetski: no output produced — a tool required the "command" permission that headless mode '
              "cannot prompt for, so it was auto-denied.", file=sys.stderr)
        sys.exit(0)
    print("Réponse de Gemini")
