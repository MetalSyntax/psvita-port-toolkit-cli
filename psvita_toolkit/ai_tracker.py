import os
import subprocess
from datetime import datetime
from pathlib import Path
from .tui import C
from .i18n import t

def track_ai_changes(project_cfg):
    """!
    @brief Detects uncommitted changes (git diff HEAD) and appends them
           to an AI_CHANGELOG.md file in the project directory, so the user
           has a record of what the AI just changed.
    """
    proj_dir = Path(project_cfg["_project_dir"])
    
    print(f"\n{C.CYAN}Analizando cambios en el directorio del proyecto...{C.RESET}")
    
    # Run git diff HEAD to get all changes (staged and unstaged)
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=proj_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )
    except FileNotFoundError:
        print(f"{C.RED}Error: no se encontró git. Asegúrate de tener git instalado.{C.RESET}")
        input(f"\n{C.DIM}Presiona Enter para continuar...{C.RESET}")
        return

    if result.returncode != 0:
        print(f"{C.RED}Error al ejecutar git diff: {result.stderr}{C.RESET}")
        input(f"\n{C.DIM}Presiona Enter para continuar...{C.RESET}")
        return
        
    diff_output = result.stdout.strip()
    if not diff_output:
        print(f"{C.YELLOW}No se detectaron cambios sin confirmar (el árbol de git está limpio).{C.RESET}")
        input(f"\n{C.DIM}Presiona Enter para continuar...{C.RESET}")
        return
        
    changelog_path = proj_dir / "AI_CHANGELOG.md"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # We will format the diff into markdown code blocks.
    # To make it more readable, we can separate it by files or just append the raw patch with a header.
    # Appending a nicely formatted diff is usually best.
    
    changelog_entry = f"\n\n## Cambio AI detectado - {timestamp}\n\n"
    changelog_entry += "```diff\n"
    changelog_entry += diff_output
    changelog_entry += "\n```\n"
    
    with open(changelog_path, "a", encoding="utf-8") as f:
        f.write(changelog_entry)
        
    print(f"{C.GREEN}✓ Cambios registrados exitosamente en: {changelog_path.name}{C.RESET}")
    print(f"{C.DIM}(Nota: Los cambios aún no están confirmados en git, solo documentados en el MD.){C.RESET}")
    
    # Optionally, we can do a diff --stat to show the user what was recorded
    stat_result = subprocess.run(
        ["git", "diff", "HEAD", "--stat"],
        cwd=proj_dir,
        stdout=subprocess.PIPE,
        text=True
    )
    if stat_result.stdout:
        print(f"\nResumen de archivos modificados:\n{stat_result.stdout.strip()}")
        
    input(f"\n{C.DIM}Presiona Enter para volver...{C.RESET}")
