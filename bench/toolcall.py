#!/usr/bin/env python3
"""Routing fidelity test: 10 prompts that should each call exactly one of three tools.

Tools:
- delegate_to_coding_agent(task) — anything code-shaped (write/refactor/debug code)
- delegate_to_general_agent(task) — general knowledge / writing / reasoning
- search_memory(query) — recalling user preferences, prior context, stored facts

Grade: model must call the expected tool. 9/10 required.
"""
import json
import sys
import urllib.request

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9292/v1"
MODEL = sys.argv[2] if len(sys.argv) > 2 else "orchestrator"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "delegate_to_coding_agent",
            "description": "Delegate any code-writing, code-refactoring, or debugging task to the specialist coding agent.",
            "parameters": {
                "type": "object",
                "properties": {"task": {"type": "string", "description": "The coding task to perform."}},
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_to_general_agent",
            "description": "Delegate general-knowledge questions, writing tasks, explanation, or reasoning that is NOT about code.",
            "parameters": {
                "type": "object",
                "properties": {"task": {"type": "string", "description": "The general task to perform."}},
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Look up stored facts about the user, prior conversations, preferences, or context.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "What to look up in memory."}},
                "required": ["query"],
            },
        },
    },
]

CASES = [
    ("Write a Python function that reverses a linked list.", "delegate_to_coding_agent"),
    ("Refactor my CSV parser to use pandas.", "delegate_to_coding_agent"),
    ("There's a NullPointerException in my Java service — help debug.", "delegate_to_coding_agent"),
    ("Explain photosynthesis in two sentences.", "delegate_to_general_agent"),
    ("Draft a polite email declining a meeting request.", "delegate_to_general_agent"),
    ("What were the main causes of World War I?", "delegate_to_general_agent"),
    ("What's my preferred Python web framework? You stored it last week.", "search_memory"),
    ("Remind me which database I'm using in the Hosur rental project.", "search_memory"),
    ("Did I mention what time zone I work in?", "search_memory"),
    ("Add input validation to the login form in my React app.", "delegate_to_coding_agent"),
]

SYSTEM = (
    "You are an orchestrator. For every user request, call exactly ONE of the available tools "
    "to delegate the work. Never answer the user directly. Pick the tool that best matches the request."
)


def run_case(user_msg: str) -> str | None:
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0.0,
        "max_tokens": 256,
    }).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=body,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
    choice = result["choices"][0]
    msg = choice.get("message", {})
    tool_calls = msg.get("tool_calls") or []
    if not tool_calls:
        return None
    return tool_calls[0].get("function", {}).get("name")


def main():
    print(f"endpoint={BASE_URL}  model={MODEL}\n")
    correct = 0
    print(f"{'#':<3} {'expected':<28} {'actual':<28} result")
    print("-" * 78)
    for i, (msg, expected) in enumerate(CASES, 1):
        try:
            actual = run_case(msg) or "<no tool call>"
        except Exception as e:
            actual = f"<error: {e}>"
        ok = actual == expected
        correct += int(ok)
        print(f"{i:<3} {expected:<28} {actual:<28} {'PASS' if ok else 'FAIL'}")
    print("-" * 78)
    print(f"score: {correct}/{len(CASES)}")


if __name__ == "__main__":
    main()
