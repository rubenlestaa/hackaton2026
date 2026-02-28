import re

URL_REGEX  = re.compile(r'https?://\S+')
CODE_SIGNS = ["def ", "import ", "function ", "SELECT ", "```", "=>", "=="]
TASK_SIGNS = ["TODO", "- [ ]", "☐", "tarea:", "hacer:"]

def classify(content: str) -> str:
    if URL_REGEX.search(content):
        return "url"
    if any(sign in content for sign in TASK_SIGNS):
        return "task"
    if any(sign in content for sign in CODE_SIGNS):
        return "code"
    if content.endswith((".mp3", ".wav", ".ogg", ".m4a")):
        return "audio"
    if content.endswith((".pdf", ".docx", ".txt")):
        return "document"
    return "note"
