from __future__ import annotations

import argparse
import dataclasses
import json
import os
import socket
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from todoist_api_python.api import TodoistAPI


@dataclass
class Config:
    api_token: str
    vault_path: Path
    output_relative_path: Path
    host_name: str
    mapping_file: Path


def load_config(env_file: str) -> Config:
    load_dotenv(env_file)

    api_token = os.getenv("TODOIST_API_TOKEN", "").strip()
    vault_path = os.getenv("VAULT_PATH", "").strip()
    output_relative = os.getenv(
        "TODOIST_CONTEXT_RELATIVE_PATH",
        "9000_Obsidian_Infrastructure/9300_AI/9310_Context",
    ).strip()
    host_name = os.getenv("HOST_NAME", "").strip() or socket.gethostname()

    if not api_token:
        raise ValueError("Missing TODOIST_API_TOKEN in environment or .env")

    if not vault_path:
        raise ValueError("Missing VAULT_PATH in environment or .env")

    script_dir = Path(__file__).resolve().parent
    mapping_file = script_dir / "todoist_note_mapping.json"

    return Config(
        api_token=api_token,
        vault_path=Path(vault_path),
        output_relative_path=Path(output_relative),
        host_name=host_name,
        mapping_file=mapping_file,
    )


def get_todoist_data(api: TodoistAPI) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    projects = [dataclasses.asdict(p) for page in api.get_projects() for p in page]
    sections = [dataclasses.asdict(s) for page in api.get_sections() for s in page]
    tasks = [dataclasses.asdict(t) for page in api.get_tasks() for t in page]
    labels = [dataclasses.asdict(l) for page in api.get_labels() for l in page]
    return projects, sections, tasks, labels


def load_note_mapping(mapping_file: Path) -> dict[str, str]:
    if not mapping_file.exists():
        return {}
    return json.loads(mapping_file.read_text(encoding="utf-8"))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_local_string() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=str(path.parent), newline="\n") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def md_escape(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", " ").strip()


def format_list(values: list[str]) -> str:
    if not values:
        return ""
    return ", ".join(values)


def task_sort_key(task: dict[str, Any]) -> tuple:
    due = task.get("due") or {}
    due_date_raw = due.get("date")
    if due_date_raw is None:
        due_date = "9999-99-99"
    else:
        due_date = str(due_date_raw)
    priority = -(task.get("priority") or 1)
    order = task.get("order") or 999999
    content = task.get("content") or ""
    return (due_date, priority, order, content.lower())


def render_header(title: str, generated_at: str, host_name: str, source: str = "Todoist API") -> list[str]:
    return [
        f"# {title}",
        "",
        "This note is generated for AI/context consumption.",
        "Todoist remains the operational source of truth.",
        "This note should not be manually edited.",
        "",
        f"- generated_at: {generated_at}",
        f"- generated_by: todoist_export.py",
        f"- host: {host_name}",
        f"- source: {source}",
        "",
    ]


def wikilink_for_project(todoist_project_name: str, mapping: dict[str, str]) -> str:
    note_title = mapping.get(todoist_project_name)
    if note_title:
        return f"[[{note_title}]]"
    return ""


def render_task_block(
    task: dict[str, Any],
    project_name: str,
    section_name: str,
    labels_by_id: dict[str, str],
    mapping: dict[str, str],
) -> list[str]:
    due = task.get("due") or {}
    label_names = [labels_by_id.get(label_id, label_id) for label_id in task.get("labels", [])]
    related_note = wikilink_for_project(project_name, mapping)

    lines = [
        "### Task",
        f"- id: {md_escape(task.get('id'))}",
        f"- content: {md_escape(task.get('content'))}",
        f"- description: {md_escape(task.get('description'))}",
        f"- project: {md_escape(project_name)}",
        f"- section: {md_escape(section_name)}",
        f"- labels: {format_list(label_names)}",
        f"- priority: {md_escape(task.get('priority'))}",
        f"- due: {md_escape(due.get('date'))}",
        f"- due_string: {md_escape(due.get('string'))}",
        f"- completed: false",
        f"- parent: {md_escape(task.get('parent_id'))}",
        f"- url: {md_escape(task.get('url'))}",
        f"- related_note: {related_note}",
        "",
    ]
    return lines


def render_context_file(
    tasks: list[dict[str, Any]],
    projects_by_id: dict[str, dict[str, Any]],
    sections_by_id: dict[str, dict[str, Any]],
    labels_by_id: dict[str, str],
    mapping: dict[str, str],
    generated_at: str,
    host_name: str,
) -> str:
    lines = render_header("Todoist Copilot Context", generated_at, host_name)

    lines.extend(
        [
            "## Summary",
            "",
            f"- total_active_tasks: {len(tasks)}",
            f"- total_projects: {len({t.get('project_id') for t in tasks})}",
            "",
            "## Tasks",
            "",
        ]
    )

    for task in sorted(tasks, key=task_sort_key):
        project_name = projects_by_id.get(task.get("project_id"), {}).get("name", "Unknown Project")
        section_name = sections_by_id.get(task.get("section_id"), {}).get("name", "")
        lines.extend(render_task_block(task, project_name, section_name, labels_by_id, mapping))

    return "\n".join(lines).strip() + "\n"


def render_inbox_file(
    tasks: list[dict[str, Any]],
    inbox_project_ids: set[str],
    projects_by_id: dict[str, dict[str, Any]],
    sections_by_id: dict[str, dict[str, Any]],
    labels_by_id: dict[str, str],
    mapping: dict[str, str],
    generated_at: str,
    host_name: str,
) -> str:
    inbox_tasks = [t for t in tasks if t.get("project_id") in inbox_project_ids]
    lines = render_header("Todoist Inbox", generated_at, host_name)

    lines.extend(
        [
            "## Summary",
            "",
            f"- inbox_task_count: {len(inbox_tasks)}",
            "",
            "## Tasks",
            "",
        ]
    )

    for task in sorted(inbox_tasks, key=task_sort_key):
        project_name = projects_by_id.get(task.get("project_id"), {}).get("name", "Inbox")
        section_name = sections_by_id.get(task.get("section_id"), {}).get("name", "")
        lines.extend(render_task_block(task, project_name, section_name, labels_by_id, mapping))

    return "\n".join(lines).strip() + "\n"


def is_waiting_for(task: dict[str, Any], labels_by_id: dict[str, str]) -> bool:
    names = {labels_by_id.get(label_id, "").lower() for label_id in task.get("labels", [])}
    content = (task.get("content") or "").lower()
    description = (task.get("description") or "").lower()
    waiting_markers = {"waiting", "@waiting", "waiting_for", "@waiting_for", "wf", "@wf"}

    if names.intersection(waiting_markers):
        return True

    if content.startswith("waiting for:") or description.startswith("waiting for:"):
        return True

    return False


def is_next_action(task: dict[str, Any], labels_by_id: dict[str, str], inbox_project_ids: set[str]) -> bool:
    if task.get("project_id") in inbox_project_ids:
        return False
    if task.get("parent_id"):
        return False
    if is_waiting_for(task, labels_by_id):
        return False
    return True


def render_next_actions_file(
    tasks: list[dict[str, Any]],
    inbox_project_ids: set[str],
    projects_by_id: dict[str, dict[str, Any]],
    sections_by_id: dict[str, dict[str, Any]],
    labels_by_id: dict[str, str],
    mapping: dict[str, str],
    generated_at: str,
    host_name: str,
) -> str:
    next_tasks = [t for t in tasks if is_next_action(t, labels_by_id, inbox_project_ids)]
    lines = render_header("Todoist Next Actions", generated_at, host_name)

    lines.extend(
        [
            "## Summary",
            "",
            f"- next_action_count: {len(next_tasks)}",
            "",
            "## Tasks",
            "",
        ]
    )

    for task in sorted(next_tasks, key=task_sort_key):
        project_name = projects_by_id.get(task.get("project_id"), {}).get("name", "Unknown Project")
        section_name = sections_by_id.get(task.get("section_id"), {}).get("name", "")
        lines.extend(render_task_block(task, project_name, section_name, labels_by_id, mapping))

    return "\n".join(lines).strip() + "\n"


def render_waiting_for_file(
    tasks: list[dict[str, Any]],
    projects_by_id: dict[str, dict[str, Any]],
    sections_by_id: dict[str, dict[str, Any]],
    labels_by_id: dict[str, str],
    mapping: dict[str, str],
    generated_at: str,
    host_name: str,
) -> str:
    waiting_tasks = [t for t in tasks if is_waiting_for(t, labels_by_id)]
    lines = render_header("Todoist Waiting For", generated_at, host_name)

    lines.extend(
        [
            "## Summary",
            "",
            f"- waiting_for_count: {len(waiting_tasks)}",
            "",
            "## Tasks",
            "",
        ]
    )

    for task in sorted(waiting_tasks, key=task_sort_key):
        project_name = projects_by_id.get(task.get("project_id"), {}).get("name", "Unknown Project")
        section_name = sections_by_id.get(task.get("section_id"), {}).get("name", "")
        lines.extend(render_task_block(task, project_name, section_name, labels_by_id, mapping))

    return "\n".join(lines).strip() + "\n"


def render_project_map_file(
    tasks: list[dict[str, Any]],
    projects_by_id: dict[str, dict[str, Any]],
    sections_by_id: dict[str, dict[str, Any]],
    labels_by_id: dict[str, str],
    mapping: dict[str, str],
    generated_at: str,
    host_name: str,
) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        project_name = projects_by_id.get(task.get("project_id"), {}).get("name", "Unknown Project")
        grouped[project_name].append(task)

    lines = render_header("Todoist Project Map", generated_at, host_name)

    lines.extend(
        [
            "## Summary",
            "",
            f"- mapped_projects: {len(grouped)}",
            "",
        ]
    )

    for project_name in sorted(grouped.keys(), key=lambda s: s.lower()):
        related_note = wikilink_for_project(project_name, mapping)
        lines.append(f"## {related_note or project_name}")
        lines.append("")
        lines.append(f"- todoist_project: {project_name}")
        if related_note:
            lines.append(f"- related_note: {related_note}")
        lines.append("")

        project_tasks = sorted(grouped[project_name], key=task_sort_key)

        next_actions = [t for t in project_tasks if not is_waiting_for(t, labels_by_id) and not t.get("parent_id")]
        waiting_for = [t for t in project_tasks if is_waiting_for(t, labels_by_id)]

        lines.append("### Active Tasks")
        lines.append("")
        if project_tasks:
            for task in project_tasks:
                due = task.get("due") or {}
                section_name = sections_by_id.get(task.get("section_id"), {}).get("name", "")
                lines.append(
                    f"- {task.get('content')} | due: {due.get('date', '')} | section: {section_name} | url: {task.get('url', '')}"
                )
        else:
            lines.append("- None")
        lines.append("")

        lines.append("### Next Actions")
        lines.append("")
        if next_actions:
            for task in next_actions:
                due = task.get("due") or {}
                lines.append(f"- {task.get('content')} | due: {due.get('date', '')}")
        else:
            lines.append("- None")
        lines.append("")

        lines.append("### Waiting For")
        lines.append("")
        if waiting_for:
            for task in waiting_for:
                due = task.get("due") or {}
                lines.append(f"- {task.get('content')} | due: {due.get('date', '')}")
        else:
            lines.append("- None")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def render_status_file(
    generated_at: str,
    host_name: str,
    status: str,
    exported_files: int,
    task_count: int,
    project_count: int,
    last_error: str = "",
) -> str:
    lines = [
        "# Todoist Sync Status",
        "",
        "This note is generated for AI/context consumption.",
        "Todoist remains the operational source of truth.",
        "This note should not be manually edited.",
        "",
        f"- last_successful_sync: {generated_at}" if status == "success" else f"- last_attempted_sync: {generated_at}",
        f"- host: {host_name}",
        f"- status: {status}",
        f"- exported_files: {exported_files}",
        f"- task_count: {task_count}",
        f"- project_count: {project_count}",
        f"- last_error: {last_error}",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Todoist tasks to Obsidian notes.")
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to the environment file (default: .env)",
    )
    args = parser.parse_args()

    config = load_config(args.env_file)
    output_dir = config.vault_path / config.output_relative_path
    generated_at = now_local_string()
    mapping = load_note_mapping(config.mapping_file)

    try:
        api = TodoistAPI(config.api_token)
        projects, sections, tasks, labels = get_todoist_data(api)

        projects_by_id = {str(p["id"]): p for p in projects}
        sections_by_id = {str(s["id"]): s for s in sections}
        labels_by_id = {str(l["id"]): l["name"] for l in labels}

        for task in tasks:
            if "project_id" in task:
                task["project_id"] = str(task["project_id"])
            if task.get("section_id") is not None:
                task["section_id"] = str(task["section_id"])
            if task.get("parent_id") is not None:
                task["parent_id"] = str(task["parent_id"])
            task["labels"] = [str(label) for label in task.get("labels", [])]

        inbox_project_ids = {
            str(p["id"])
            for p in projects
            if p.get("is_inbox_project") is True
        }

        files = {
            "9310_Todoist_Copilot_Context.md": render_context_file(
                tasks, projects_by_id, sections_by_id, labels_by_id, mapping, generated_at, config.host_name
            ),
            "9311_Todoist_Inbox.md": render_inbox_file(
                tasks, inbox_project_ids, projects_by_id, sections_by_id, labels_by_id, mapping, generated_at, config.host_name
            ),
            "9312_Todoist_Next_Actions.md": render_next_actions_file(
                tasks, inbox_project_ids, projects_by_id, sections_by_id, labels_by_id, mapping, generated_at, config.host_name
            ),
            "9313_Todoist_Waiting_For.md": render_waiting_for_file(
                tasks, projects_by_id, sections_by_id, labels_by_id, mapping, generated_at, config.host_name
            ),
            "9314_Todoist_Project_Map.md": render_project_map_file(
                tasks, projects_by_id, sections_by_id, labels_by_id, mapping, generated_at, config.host_name
            ),
        }

        for filename, content in files.items():
            atomic_write(output_dir / filename, content)

        status_content = render_status_file(
            generated_at=generated_at,
            host_name=config.host_name,
            status="success",
            exported_files=len(files),
            task_count=len(tasks),
            project_count=len(projects),
            last_error="",
        )
        atomic_write(output_dir / "9319_Todoist_Sync_Status.md", status_content)

        print(f"Export complete. Wrote {len(files) + 1} files to {output_dir}")

    except Exception as exc:
        output_dir.mkdir(parents=True, exist_ok=True)
        status_content = render_status_file(
            generated_at=generated_at,
            host_name=config.host_name,
            status="error",
            exported_files=0,
            task_count=0,
            project_count=0,
            last_error=str(exc),
        )
        atomic_write(output_dir / "9319_Todoist_Sync_Status.md", status_content)
        raise


if __name__ == "__main__":
    main()