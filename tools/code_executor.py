"""Sandboxed arithmetic/logic executor - fallback for computation challenges.

The earlier version called exec() on agent-supplied source with no
restrictions. Inside a Lambda that means submitted code could import os,
read environment variables, reach the network, or use the function's IAM
role. This version validates the AST against an allowlist first and never
exposes real builtins.

Use the dedicated tools (permit_calculator, claims_eob_solver,
course_optimizer) where they apply. This is the catch-all for arithmetic
variants those tools do not cover.

Deploy notes: give this function no AWS permissions beyond logging, no VPC
access, a short timeout, and no sensitive environment variables.
"""

import ast
import io
import json
import sys
from typing import Any, Dict, Set

MAX_SOURCE_CHARS = 4000
MAX_OUTPUT_CHARS = 1000
MAX_AST_NODES = 1200

ALLOWED_NODES = (
    ast.Module, ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign,
    ast.Name, ast.Load, ast.Store, ast.Constant,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.BitAnd, ast.BitOr, ast.BitXor, ast.LShift, ast.RShift, ast.Invert,
    ast.List, ast.Tuple, ast.Dict, ast.Set, ast.Subscript, ast.Slice, ast.Index,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension,
    ast.For, ast.While, ast.If, ast.Break, ast.Continue, ast.Pass,
    ast.FunctionDef, ast.Return, ast.arguments, ast.arg, ast.Call, ast.keyword,
    ast.Starred, ast.Lambda,
)

FORBIDDEN_NODES = (
    ast.Import, ast.ImportFrom, ast.Attribute, ast.ClassDef, ast.Global,
    ast.Nonlocal, ast.Delete, ast.With, ast.AsyncWith, ast.Try, ast.Raise,
    ast.Assert, ast.AsyncFunctionDef, ast.Await, ast.Yield, ast.YieldFrom,
)

SAFE_BUILTINS: Dict[str, Any] = {
    name: getattr(__builtins__, name) if hasattr(__builtins__, name) else __builtins__[name]
    for name in (
        "abs", "all", "any", "bool", "divmod", "enumerate", "filter", "float",
        "int", "len", "list", "dict", "set", "tuple", "map", "max", "min",
        "pow", "range", "reversed", "round", "sorted", "str", "sum", "zip",
        "print", "isinstance", "repr",
    )
}


class UnsafeCodeError(ValueError):
    """Raised when submitted source uses a construct outside the allowlist."""


def validate(source: str) -> ast.Module:
    """Parse and allowlist-check the source, or raise UnsafeCodeError."""
    if not source or not source.strip():
        raise UnsafeCodeError("no code provided")
    if len(source) > MAX_SOURCE_CHARS:
        raise UnsafeCodeError(f"source exceeds {MAX_SOURCE_CHARS} characters")

    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise UnsafeCodeError(f"syntax error: {exc.msg}") from exc

    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_AST_NODES:
        raise UnsafeCodeError("code is too complex")

    for node in nodes:
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeCodeError(f"{type(node).__name__} is not permitted")
        if not isinstance(node, ALLOWED_NODES):
            raise UnsafeCodeError(f"{type(node).__name__} is not permitted")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise UnsafeCodeError("dunder names are not permitted")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "__" in node.value and len(node.value) > 6:
                raise UnsafeCodeError("dunder strings are not permitted")

    return tree


def run(source: str) -> str:
    """Execute validated source and return captured stdout."""
    tree = validate(source)
    compiled = compile(tree, "<challenge>", "exec")

    namespace: Dict[str, Any] = {"__builtins__": SAFE_BUILTINS}
    buffer = io.StringIO()
    original_stdout = sys.stdout
    sys.stdout = buffer
    try:
        exec(compiled, namespace, namespace)  # noqa: S102 - AST-validated above
    except Exception as exc:  # noqa: BLE001 - report to the agent, never crash
        return f"Execution Error: {exc}"
    finally:
        sys.stdout = original_stdout

    output = buffer.getvalue().strip()
    return output[:MAX_OUTPUT_CHARS] if output else "Executed successfully but printed nothing."


def _parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(event, dict) and "body" in event:
        body = event["body"]
        if isinstance(body, str):
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"code": body}
        if isinstance(body, dict):
            return body
    if isinstance(event, dict) and "parameters" in event:
        return {p.get("name"): p.get("value") for p in event.get("parameters") or []}
    return event if isinstance(event, dict) else {}


def lambda_handler(event, context):
    """Accepts {"code": "..."} and returns the printed output."""
    try:
        body = _parse_body(event)
        code = body.get("code")
        if not code:
            return {"statusCode": 400, "body": json.dumps({"error": "no code provided"})}
        return {"statusCode": 200, "body": json.dumps({"answer": run(str(code))})}
    except UnsafeCodeError as exc:
        return {"statusCode": 400, "body": json.dumps({"error": str(exc)})}
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return {"statusCode": 500, "body": json.dumps({"error": str(exc)})}
