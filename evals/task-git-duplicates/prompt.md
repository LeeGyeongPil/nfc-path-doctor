---
name: task-git-duplicates
max_turns: 14
---

The file dup.zip in this folder is a git repository from my Mac. Unpack it into the current folder first with `python3 -m zipfile -e dup.zip .`. git ls-files seems to list the same file name twice. Tell me exactly which index paths clash, including their raw bytes in hex, and leave the repository unchanged. Save the full machine-readable findings to report.json in the current folder.
