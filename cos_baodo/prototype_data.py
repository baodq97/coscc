"""Invented, in-memory examples for COS Studio. Never a source of real workspace state."""

from dataclasses import dataclass, field


@dataclass
class Workspace:
    id: str
    name: str
    description: str
    color: str = "iris"
    initials: str = "WS"
    repository: str = "Local demo workspace"


@dataclass
class Work:
    id: str
    workspace: str
    title: str
    summary: str
    lane: str
    stage: str
    color: str
    owner: str = "You"
    tokens: int = 0
    cost: float = 0.0
    progress: int = 0
    mode: str = "manual"


@dataclass
class Message:
    role: str
    text: str


@dataclass
class Conversation:
    id: str
    workspace: str
    title: str
    subtitle: str
    messages: list[Message] = field(default_factory=list)


@dataclass
class Activity:
    workspace: str
    title: str
    detail: str
    icon: str = "circle-check"
    color: str = "iris"
    time: str = "Sample event"


def workspaces() -> list[Workspace]:
    return [
        Workspace("atlas", "Atlas", "A thoughtful home for human + AI work.", "iris", "AT",
                  "demo / atlas-studio"),
        Workspace("fieldnotes", "Fieldnotes", "Small observations. Better decisions.", "grass", "FN",
                  "demo / fieldnotes"),
        Workspace("orbit", "Orbit API", "The quiet infrastructure behind the product.", "blue", "OR",
                  "demo / orbit-api"),
    ]


def work() -> list[Work]:
    return [
        Work("COS-014", "atlas", "A home for every workspace",
             "Make moving between projects feel effortless, without losing your place.",
             "In progress", "impl", "iris", "AI", 28400, 0.42, 58, "autonomous"),
        Work("COS-013", "atlas", "Make runs feel live",
             "A clear view of what the agent is doing, and what needs you next.",
             "Needs review", "spec", "amber", "You", 12600, 0.19, 28),
        Work("COS-012", "atlas", "A command away",
             "Find a workspace, a conversation or your next step from one place.",
             "Planned", "intent", "gray", "You", 4200, 0.06, 14),
        Work("COS-011", "atlas", "Sessions with a memory",
             "Pick up the conversation exactly where you left it.",
             "Planned", "plan", "blue", "AI", 8100, 0.12, 42, "autonomous"),
        Work("COS-010", "atlas", "Safer autonomous steps",
             "Show the scope of an action before the agent takes it.",
             "Needs review", "review", "amber", "You", 19200, 0.29, 86),
        Work("COS-009", "atlas", "A quieter dark mode",
             "Less glare. The same clarity. An appearance that stays out of the way.",
             "Complete", "ship", "grass", "You", 6400, 0.09, 100),
        Work("NOTE-003", "fieldnotes", "Capture the small frictions",
             "Give unfinished observations somewhere useful to live.",
             "In progress", "spec", "iris", "AI", 3200, 0.05, 28, "autonomous"),
        Work("NOTE-002", "fieldnotes", "An index for good ideas",
             "A searchable library of the things worth coming back to.",
             "Planned", "intent", "gray", "You", 1100, 0.02, 14),
    ]


def conversations() -> list[Conversation]:
    return [
        Conversation("atlas-1", "atlas", "Workspace navigation", "Exploring a calmer app shell", [
            Message("you", "How can switching projects feel less disruptive?"),
            Message("assistant", "Keep the workspace visible, preserve its context, and let details "
                    "open beside the work instead of replacing it.\n\nFor this concept, the sidebar "
                    "anchors navigation while the board stays your home base."),
        ]),
        Conversation("atlas-2", "atlas", "Thinking through the board", "Finding the right level of detail", [
            Message("you", "What belongs on a work card?"),
            Message("assistant", "The outcome, the current stage, and who needs to act next. "
                    "Artifacts and the full timeline can live one click deeper."),
        ]),
        Conversation("fieldnotes-1", "fieldnotes", "A place for observations", "Before an idea becomes a plan", [
            Message("assistant", "Start with what you noticed. It does not need a solution yet."),
        ]),
    ]


def activities() -> list[Activity]:
    return [
        Activity("atlas", "Implementation started", "COS-014 / A home for every workspace",
                 "zap", "iris", "12 min ago (sample)"),
        Activity("atlas", "A spec is ready for your attention", "COS-013 / Make runs feel live",
                 "message-circle", "amber", "38 min ago (sample)"),
        Activity("atlas", "Plan accepted by agent", "COS-011 / Sessions with a memory",
                 "file-check", "blue", "1 hour ago (sample)"),
        Activity("atlas", "Appearance exploration completed", "COS-009 / A quieter dark mode",
                 "circle-check", "grass", "2 hours ago (sample)"),
        Activity("fieldnotes", "A new observation captured", "NOTE-003 / Capture the small frictions",
                 "lightbulb", "grass", "20 min ago (sample)"),
    ]
