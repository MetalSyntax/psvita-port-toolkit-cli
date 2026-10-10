"""!
@file init_port.py
@brief Wizard to create a new PS Vita port from scratch: prerequisite checks,
       prompts, cloning `soloader-boilerplate`, APK extraction/ABI/GLES
       detection, decompilation, `git init`, and generating
       `PORTING_PLAN.md`/`port_progress.md`/`CLAUDE.md`.

@details
This is a Python port of `init_new_port.sh`, parametrized by the global
config instead of hardcoded paths. Unlike that script, it does NOT copy
`porting_tools/` into the new port -- the new port only needs the
`.psvita-toolkit.json` this wizard writes at the end for the rest of the
toolkit (`build_deploy`, `ftp_ops`, `livearea`, `crash_analyzer`, ...) to
operate on it from outside. The only things copied INTO the port's own repo
are the Claude Code skills and the `soloader-boilerplate` scaffold itself
(`source/`, `lib/`, `CMakeLists.txt`), since those are genuinely the port's
own source code.

See `docs/dev-notes/init_port.md` for why this diverges from the original
script this way, and the real bugs fixed in `_same_file()`,
`_merge_tree_no_clobber()`, and the TITLEID-reuse flow in `prompt_inputs()`.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config as cfgmod
from . import i18n
from . import tui
from .i18n import t
from .tui import C

STRINGS = {
    "init_port.checking_prereqs": {
        "es": "Verificando prerrequisitos...",
        "en": "Checking prerequisites...",
        "pt": "Verificando pré-requisitos...",
    },
    "init_port.analyzing_engine_title": {
        "es": "Analizando motor y generando candidatos de stubs JNI...",
        "en": "Analyzing engine and generating JNI stub candidates...",
        "pt": "Analisando o motor e gerando candidatos de stubs JNI...",
    },
    "init_port.jadx_found": {
        "es": "encontrado",
        "en": "found",
        "pt": "encontrado",
    },
    "init_port.jadx_missing": {
        "es": "(brew install jadx) -- se podrá correr manualmente después",
        "en": "(brew install jadx) -- can be run manually afterward",
        "pt": "(brew install jadx) -- poderá ser executado manualmente depois",
    },
    "init_port.docker_so_found": {
        "es": "encontrados",
        "en": "found",
        "pt": "encontrados",
    },
    "init_port.docker_so_missing": {
        "es": "(falta la imagen -- docker pull devrvk/so-decompiler)",
        "en": "(missing the image -- docker pull devrvk/so-decompiler)",
        "pt": "(falta a imagem -- docker pull devrvk/so-decompiler)",
    },
    "init_port.docker_not_found": {
        "es": "docker no encontrado -- la decompilación de .so se podrá correr manualmente después.",
        "en": "docker not found -- .so decompilation can be run manually afterward.",
        "pt": "docker não encontrado -- a decompilação de .so poderá ser executada manualmente depois.",
    },
    "init_port.tool_not_installed": {
        "es": "'{tool}' no está instalado, no se puede continuar.",
        "en": "'{tool}' is not installed, cannot continue.",
        "pt": "'{tool}' não está instalado, não é possível continuar.",
    },
    "init_port.boilerplate_not_found": {
        "es": "No se encontró soloader-boilerplate en {path}",
        "en": "soloader-boilerplate not found at {path}",
        "pt": "soloader-boilerplate não encontrado em {path}",
    },
    "init_port.wizard_title": {
        "es": "Crear port nuevo: Android → PS Vita",
        "en": "Create new port: Android → PS Vita",
        "pt": "Criar novo port: Android → PS Vita",
    },
    "init_port.game_name_prompt": {
        "es": "Nombre del juego (display, ej. 'Inotia 4'):",
        "en": "Game name (display, e.g. 'Inotia 4'):",
        "pt": "Nome do jogo (exibição, ex. 'Inotia 4'):",
    },
    "init_port.game_name_required": {
        "es": "El nombre del juego es obligatorio.",
        "en": "The game name is required.",
        "pt": "O nome do jogo é obrigatório.",
    },
    "init_port.slug_prompt": {
        "es": "Slug corto interno, sin espacios",
        "en": "Short internal slug, no spaces",
        "pt": "Slug curto interno, sem espaços",
    },
    "init_port.folder_prompt": {
        "es": "Nombre de la carpeta del proyecto",
        "en": "Project folder name",
        "pt": "Nome da pasta do projeto",
    },
    "init_port.apk_path_prompt": {
        "es": "Ruta absoluta al .apk original:",
        "en": "Absolute path to the original .apk:",
        "pt": "Caminho absoluto para o .apk original:",
    },
    "init_port.vita_ip_prompt": {
        "es": "IP de la PS Vita de pruebas",
        "en": "IP of the test PS Vita",
        "pt": "IP da PS Vita de testes",
    },
    "init_port.titleids_used_header": {
        "es": "TITLEIDs ya usados en {base_dir} (no reusar, colisiona en LiveArea):",
        "en": "TITLEIDs already used in {base_dir} (don't reuse, collides in LiveArea):",
        "pt": "TITLEIDs já usados em {base_dir} (não reutilizar, colide no LiveArea):",
    },
    "init_port.own_titleid_notice": {
        "es": "[!] {new_dir} ya tiene TITLEID '{own_id}' de un intento anterior -- Enter para reusarlo y continuar el mismo proyecto.",
        "en": "[!] {new_dir} already has TITLEID '{own_id}' from a previous attempt -- press Enter to reuse it and continue the same project.",
        "pt": "[!] {new_dir} já tem o TITLEID '{own_id}' de uma tentativa anterior -- Enter para reutilizá-lo e continuar o mesmo projeto.",
    },
    "init_port.titleid_prompt": {
        "es": "TITLEID (9 caracteres alfanuméricos, ej. PSVXX0001)",
        "en": "TITLEID (9 alphanumeric characters, e.g. PSVXX0001)",
        "pt": "TITLEID (9 caracteres alfanuméricos, ex. PSVXX0001)",
    },
    "init_port.titleid_length_error": {
        "es": "Debe tener exactamente 9 caracteres.",
        "en": "Must be exactly 9 characters.",
        "pt": "Deve ter exatamente 9 caracteres.",
    },
    "init_port.titleid_in_use": {
        "es": "Ese TITLEID ya está en uso -- elegí otro.",
        "en": "That TITLEID is already in use -- choose another.",
        "pt": "Esse TITLEID já está em uso -- escolha outro.",
    },
    "init_port.dir_exists_reuse": {
        "es": "[!] Ya existe {new_dir} -- se reutiliza tal cual está y se continúa.",
        "en": "[!] {new_dir} already exists -- it will be reused as-is and continued.",
        "pt": "[!] {new_dir} já existe -- será reutilizado como está e continuado.",
    },
    "init_port.summary_title": {
        "es": "Resumen:",
        "en": "Summary:",
        "pt": "Resumo:",
    },
    "init_port.summary_game": {
        "es": "  Juego:    {name}",
        "en": "  Game:     {name}",
        "pt": "  Jogo:     {name}",
    },
    "init_port.summary_slug": {
        "es": "  Slug:     {slug}",
        "en": "  Slug:     {slug}",
        "pt": "  Slug:     {slug}",
    },
    "init_port.summary_folder": {
        "es": "  Carpeta:  {folder}",
        "en": "  Folder:   {folder}",
        "pt": "  Pasta:    {folder}",
    },
    "init_port.summary_project": {
        "es": "  Proyecto: {project}",
        "en": "  Project:  {project}",
        "pt": "  Projeto:  {project}",
    },
    "init_port.summary_apk": {
        "es": "  APK:      {apk}",
        "en": "  APK:      {apk}",
        "pt": "  APK:      {apk}",
    },
    "init_port.summary_titleid": {
        "es": "  TITLEID:  {titleid}",
        "en": "  TITLEID:  {titleid}",
        "pt": "  TITLEID:  {titleid}",
    },
    "init_port.summary_vita_ip": {
        "es": "  Vita IP:  {ip}",
        "en": "  Vita IP:  {ip}",
        "pt": "  Vita IP:  {ip}",
    },
    "init_port.confirm_continue": {
        "es": "¿Continuar?",
        "en": "Continue?",
        "pt": "Continuar?",
    },
    "init_port.cancelled_by_user": {
        "es": "Cancelado por el usuario.",
        "en": "Cancelled by user.",
        "pt": "Cancelado pelo usuário.",
    },
    "init_port.already_git_repo": {
        "es": "[!] {new_dir} ya es un repo git -- se deja como está.",
        "en": "[!] {new_dir} is already a git repo -- leaving it as is.",
        "pt": "[!] {new_dir} já é um repo git -- deixado como está.",
    },
    "init_port.dir_exists_merge": {
        "es": "[*] {new_dir} ya existe y tiene contenido -- se mergea el scaffold sin pisar nada.",
        "en": "[*] {new_dir} already exists and has content -- merging the scaffold without overwriting anything.",
        "pt": "[*] {new_dir} já existe e tem conteúdo -- o scaffold será mesclado sem sobrescrever nada.",
    },
    "init_port.cloning_boilerplate": {
        "es": "[*] Clonando soloader-boilerplate en {new_dir} ...",
        "en": "[*] Cloning soloader-boilerplate into {new_dir} ...",
        "pt": "[*] Clonando soloader-boilerplate em {new_dir} ...",
    },
    "init_port.init_submodule": {
        "es": "[*] Inicializando submódulo lib/falso_jni (requiere red)...",
        "en": "[*] Initializing lib/falso_jni submodule (needs network)...",
        "pt": "[*] Inicializando o submódulo lib/falso_jni (requer rede)...",
    },
    "init_port.submodule_failed": {
        "es": "[!] No se pudo bajar el submódulo (¿sin red?) -- correr manualmente después.",
        "en": "[!] Couldn't fetch the submodule (no network?) -- run manually afterward.",
        "pt": "[!] Não foi possível baixar o submódulo (sem rede?) -- executar manualmente depois.",
    },
    "init_port.adapting_cmake": {
        "es": "[*] Adaptando CMakeLists.txt (VITA_APP_NAME/VITA_TITLEID/project/DATA_PATH)...",
        "en": "[*] Adapting CMakeLists.txt (VITA_APP_NAME/VITA_TITLEID/project/DATA_PATH)...",
        "pt": "[*] Adaptando CMakeLists.txt (VITA_APP_NAME/VITA_TITLEID/project/DATA_PATH)...",
    },
    "init_port.cmake_adapted": {
        "es": "[+] CMakeLists.txt adaptado.",
        "en": "[+] CMakeLists.txt adapted.",
        "pt": "[+] CMakeLists.txt adaptado.",
    },
    "init_port.copying_apk": {
        "es": "[*] Copiando .apk (y su .zip gemelo)...",
        "en": "[*] Copying .apk (and its twin .zip)...",
        "pt": "[*] Copiando .apk (e seu .zip gêmeo)...",
    },
    "init_port.extracting_apk": {
        "es": "[*] Extrayendo APK a {dirname}/ ...",
        "en": "[*] Extracting APK to {dirname}/ ...",
        "pt": "[*] Extraindo APK para {dirname}/ ...",
    },
    "init_port.none": {
        "es": "ninguna",
        "en": "none",
        "pt": "nenhuma",
    },
    "init_port.abis_found": {
        "es": "[*] ABIs nativas encontradas: {abis}",
        "en": "[*] Native ABIs found: {abis}",
        "pt": "[*] ABIs nativas encontradas: {abis}",
    },
    "init_port.arch_note_no_abi": {
        "es": "No se encontró lib/<abi>/ nativo -- confirmar si el juego tiene motor nativo antes de asumir soloader.",
        "en": "No native lib/<abi>/ found -- confirm whether the game has a native engine before assuming soloader.",
        "pt": "Não foi encontrado lib/<abi>/ nativo -- confirmar se o jogo tem motor nativo antes de assumir soloader.",
    },
    "init_port.arch_note_v7a": {
        "es": "armeabi-v7a presente -> ARMv7 (hard-float/NEON disponible). El CPU de Vita (Cortex-A9) corre esto sin traducción.",
        "en": "armeabi-v7a present -> ARMv7 (hard-float/NEON available). The Vita's CPU (Cortex-A9) runs this without translation.",
        "pt": "armeabi-v7a presente -> ARMv7 (hard-float/NEON disponível). A CPU da Vita (Cortex-A9) executa isso sem tradução.",
    },
    "init_port.arch_note_v6": {
        "es": "Solo armeabi (ARMv6, soft-float) -- Vita lo ejecuta igual (ARMv7 es superset), sin NEON de v7a.",
        "en": "Only armeabi (ARMv6, soft-float) -- the Vita still runs it (ARMv7 is a superset), without v7a's NEON.",
        "pt": "Apenas armeabi (ARMv6, soft-float) -- a Vita executa do mesmo jeito (ARMv7 é um superset), sem o NEON do v7a.",
    },
    "init_port.arch_note_multi_abi": {
        "es": " Hay más de una ABI ({abis}) -- se eligió {preferred} para el análisis.",
        "en": " More than one ABI was found ({abis}) -- {preferred} was chosen for the analysis.",
        "pt": " Há mais de uma ABI ({abis}) -- {preferred} foi escolhida para a análise.",
    },
    "init_port.so_files_found": {
        "es": "[*] .so encontrados en lib/{abi}/:",
        "en": "[*] .so files found in lib/{abi}/:",
        "pt": "[*] .so encontrados em lib/{abi}/:",
    },
    "init_port.gles_undetermined": {
        "es": "sin determinar",
        "en": "undetermined",
        "pt": "sem determinar",
    },
    "init_port.gles3_detected": {
        "es": "GLES3 (glGenVertexArrays/glDrawArraysInstanced presentes)",
        "en": "GLES3 (glGenVertexArrays/glDrawArraysInstanced present)",
        "pt": "GLES3 (glGenVertexArrays/glDrawArraysInstanced presentes)",
    },
    "init_port.gles2_detected": {
        "es": "GLES2 (glCreateShader/glCreateProgram/glUseProgram -- pipeline programable)",
        "en": "GLES2 (glCreateShader/glCreateProgram/glUseProgram -- programmable pipeline)",
        "pt": "GLES2 (glCreateShader/glCreateProgram/glUseProgram -- pipeline programável)",
    },
    "init_port.gles1_detected": {
        "es": "GLES1 (pipeline fijo: glVertexPointer/glClearColorx/glTexParameterx)",
        "en": "GLES1 (fixed pipeline: glVertexPointer/glClearColorx/glTexParameterx)",
        "pt": "GLES1 (pipeline fixo: glVertexPointer/glClearColorx/glTexParameterx)",
    },
    "init_port.gles_no_signal": {
        "es": "sin señal clara por símbolos (posible Unity/libil2cpp) -- revisar con Ghidra",
        "en": "no clear signal from symbols (possibly Unity/libil2cpp) -- check with Ghidra",
        "pt": "sem sinal claro pelos símbolos (possível Unity/libil2cpp) -- revisar com Ghidra",
    },
    "init_port.gl_heuristic": {
        "es": "[*] Heurística de símbolos GL: {gles_hint}",
        "en": "[*] GL symbol heuristic: {gles_hint}",
        "pt": "[*] Heurística de símbolos GL: {gles_hint}",
    },
    "init_port.decompiling_java": {
        "es": "[*] Decompilando Java del APK con jadx (puede tardar unos minutos)...",
        "en": "[*] Decompiling the APK's Java with jadx (may take a few minutes)...",
        "pt": "[*] Decompilando o Java do APK com jadx (pode levar alguns minutos)...",
    },
    "init_port.jadx_ok": {
        "es": "[+] jadx terminó sin errores.",
        "en": "[+] jadx finished without errors.",
        "pt": "[+] jadx terminou sem erros.",
    },
    "init_port.jadx_errors": {
        "es": "[!] jadx terminó con errores (normal si son solo SDKs de ads/analytics).",
        "en": "[!] jadx finished with errors (normal if they're just ads/analytics SDKs).",
        "pt": "[!] jadx terminou com erros (normal se forem apenas SDKs de ads/analytics).",
    },
    "init_port.gles_nonstandard": {
        "es": "valor no estándar en manifest: {value}",
        "en": "non-standard value in manifest: {value}",
        "pt": "valor não padrão no manifest: {value}",
    },
    "init_port.gles_declared_suffix": {
        "es": " (declarado en AndroidManifest.xml)",
        "en": " (declared in AndroidManifest.xml)",
        "pt": " (declarado no AndroidManifest.xml)",
    },
    "init_port.gles_no_manifest_declare": {
        "es": "AndroidManifest.xml no declara glEsVersion -- usar heurística ({gles_hint})",
        "en": "AndroidManifest.xml doesn't declare glEsVersion -- using heuristic ({gles_hint})",
        "pt": "AndroidManifest.xml não declara glEsVersion -- usar heurística ({gles_hint})",
    },
    "init_port.gles_manifest_unreadable": {
        "es": "no se pudo leer AndroidManifest.xml decodificado -- usar heurística ({gles_hint})",
        "en": "couldn't read the decoded AndroidManifest.xml -- using heuristic ({gles_hint})",
        "pt": "não foi possível ler o AndroidManifest.xml decodificado -- usar heurística ({gles_hint})",
    },
    "init_port.gles_no_jadx": {
        "es": "jadx no corrió -- heurística de símbolos solamente ({gles_hint})",
        "en": "jadx didn't run -- symbol heuristic only ({gles_hint})",
        "pt": "jadx não foi executado -- apenas heurística de símbolos ({gles_hint})",
    },
    "init_port.gles_final_version": {
        "es": "[+] Versión de GLES determinada: {gles}",
        "en": "[+] Determined GLES version: {gles}",
        "pt": "[+] Versão de GLES determinada: {gles}",
    },
    "init_port.decompiling_so": {
        "es": "[*] Decompilando {name} ({abi}) con Ghidra headless (puede tardar varios minutos)...",
        "en": "[*] Decompiling {name} ({abi}) with Ghidra headless (may take several minutes)...",
        "pt": "[*] Decompilando {name} ({abi}) com Ghidra headless (pode levar vários minutos)...",
    },
    "init_port.so_decompile_ok": {
        "es": "[+] Listo: {path}",
        "en": "[+] Done: {path}",
        "pt": "[+] Pronto: {path}",
    },
    "init_port.so_decompile_failed": {
        "es": "[!] Falló la decompilación de {name}",
        "en": "[!] Decompilation of {name} failed",
        "pt": "[!] Falhou a decompilação de {name}",
    },
    "init_port.so_decompile_skipped": {
        "es": "[!] Se omite la decompilación de .so (docker/imagen no disponibles) -- correr manualmente después.",
        "en": "[!] Skipping .so decompilation (docker/image not available) -- run manually afterward.",
        "pt": "[!] Decompilação de .so ignorada (docker/imagem não disponíveis) -- executar manualmente depois.",
    },
    "init_port.so_decompile_skipped_flag": {
        "es": "[*] Saltando decompilación con Ghidra (--skip-ghidra).",
        "en": "[*] Skipping Ghidra decompilation (--skip-ghidra).",
        "pt": "[*] Pulando descompilação com Ghidra (--skip-ghidra).",
    },
    "init_port.so_decompile_interrupted": {
        "es": "[!] Decompilación con Ghidra interrumpida por el usuario (Ctrl+C). Continuando con el resto del port...",
        "en": "[!] Ghidra decompilation interrupted by user (Ctrl+C). Continuing with remaining port steps...",
        "pt": "[!] Decompilação com Ghidra interrompida pelo usuário (Ctrl+C). Continuando com as etapas restantes do port...",
    },
    "init_port.jadx_already_done": {
        "es": "[+] Java ya decompilado en decompiled/apk_jadx (reutilizando).",
        "en": "[+] Java already decompiled in decompiled/apk_jadx (reusing).",
        "pt": "[+] Java já descompilado em decompiled/apk_jadx (reutilizando).",
    },
    "init_port.jadx_interrupted": {
        "es": "[!] Decompilación Java con jadx interrumpida por el usuario (Ctrl+C). Continuando...",
        "en": "[!] Java decompilation with jadx interrupted by user (Ctrl+C). Continuing...",
        "pt": "[!] Decompilação Java com jadx interrompida pelo usuário (Ctrl+C). Continuando...",
    },
    "init_port.completing_port": {
        "es": "[*] Auditando y completando port en {path}...",
        "en": "[*] Auditing and completing port at {path}...",
        "pt": "[*] Auditando e completando port em {path}...",
    },
    "init_port.port_complete_ok": {
        "es": "[+] Port completado con éxito.",
        "en": "[+] Port completed successfully.",
        "pt": "[+] Port completado com sucesso.",
    },
    "init_port.prompt_skip_ghidra": {
        "es": "¿Deseas decompilar librerías .so con Ghidra ahora (puede tardar varios minutos)?",
        "en": "Decompile .so libraries with Ghidra now (may take several minutes)?",
        "pt": "Descompilar bibliotecas .so com Ghidra agora (pode levar vários minutos)?",
    },
    "init_port.git_init_start": {
        "es": "[*] git init + .gitignore anti-DMCA...",
        "en": "[*] git init + anti-DMCA .gitignore...",
        "pt": "[*] git init + .gitignore anti-DMCA...",
    },
    "init_port.gitignore_written": {
        "es": "[+] .gitignore escrito.",
        "en": "[+] .gitignore written.",
        "pt": "[+] .gitignore escrito.",
    },
    "init_port.plan_title": {
        "es": "Plan de Port — {game_name} (PS Vita)",
        "en": "Port Plan — {game_name} (PS Vita)",
        "pt": "Plano de Port — {game_name} (PS Vita)",
    },
    "init_port.plan_intro": {
        "es": "Generado por psvita-port-toolkit el {today}. Punto de partida con lo detectado automáticamente --\nconfirmar todo con objdump/Ghidra/jadx a mano antes de asumirlo como cierto.",
        "en": "Generated by psvita-port-toolkit on {today}. A starting point based on what was auto-detected --\nconfirm everything by hand with objdump/Ghidra/jadx before assuming it's accurate.",
        "pt": "Gerado pelo psvita-port-toolkit em {today}. Ponto de partida com o que foi detectado automaticamente --\nconfirme tudo manualmente com objdump/Ghidra/jadx antes de assumir como certo.",
    },
    "init_port.plan_section0_title": {
        "es": "0. Contexto",
        "en": "0. Context",
        "pt": "0. Contexto",
    },
    "init_port.plan_game_label": {
        "es": "Juego:",
        "en": "Game:",
        "pt": "Jogo:",
    },
    "init_port.plan_package_label": {
        "es": "Paquete Java:",
        "en": "Java package:",
        "pt": "Pacote Java:",
    },
    "init_port.plan_package_pending": {
        "es": "(pendiente, ver decompiled/apk_jadx/resources/AndroidManifest.xml)",
        "en": "(pending, see decompiled/apk_jadx/resources/AndroidManifest.xml)",
        "pt": "(pendente, ver decompiled/apk_jadx/resources/AndroidManifest.xml)",
    },
    "init_port.plan_apk_label": {
        "es": "APK original:",
        "en": "Original APK:",
        "pt": "APK original:",
    },
    "init_port.plan_titleid_label": {
        "es": "TITLEID asignado:",
        "en": "Assigned TITLEID:",
        "pt": "TITLEID atribuído:",
    },
    "init_port.plan_engine_known": {
        "es": "**¿Motor conocido?** Revisar si algún port hermano (bajo la misma BASE_DIR) comparte motor antes de\nreusar su código -- confirmar con símbolos JNI reales, no por analogía superficial.",
        "en": "**Known engine?** Check whether any sibling port (under the same BASE_DIR) shares the engine before\nreusing its code -- confirm with real JNI symbols, not by superficial analogy.",
        "pt": "**Motor conhecido?** Verificar se algum port irmão (sob o mesmo BASE_DIR) compartilha o motor antes de\nreutilizar seu código -- confirmar com símbolos JNI reais, não por analogia superficial.",
    },
    "init_port.plan_section1_title": {
        "es": "1. Detección automática",
        "en": "1. Automatic detection",
        "pt": "1. Detecção automática",
    },
    "init_port.plan_abi_label": {
        "es": "ABI(s):",
        "en": "ABI(s):",
        "pt": "ABI(s):",
    },
    "init_port.plan_abi_chosen_label": {
        "es": "ABI elegida:",
        "en": "Chosen ABI:",
        "pt": "ABI escolhida:",
    },
    "init_port.plan_arch_note_label": {
        "es": "Nota de arquitectura:",
        "en": "Architecture note:",
        "pt": "Nota de arquitetura:",
    },
    "init_port.plan_gles_label": {
        "es": "Versión de GLES:",
        "en": "GLES version:",
        "pt": "Versão do GLES:",
    },
    "init_port.plan_section2_title": {
        "es": "2. .so encontrados (ABI {abi})",
        "en": "2. .so files found (ABI {abi})",
        "pt": "2. .so encontrados (ABI {abi})",
    },
    "init_port.plan_so_none": {
        "es": "(ninguno detectado automáticamente -- revisar la extracción a mano)\n",
        "en": "(none detected automatically -- check the extraction by hand)\n",
        "pt": "(nenhum detectado automaticamente -- revisar a extração manualmente)\n",
    },
    "init_port.plan_section3_title": {
        "es": "3. Exports JNI (convención `Java_*`)",
        "en": "3. JNI exports (`Java_*` convention)",
        "pt": "3. Exports JNI (convenção `Java_*`)",
    },
    "init_port.plan_jni_none": {
        "es": "(no se encontraron exports `Java_*` -- confirmar a mano con objdump -T, puede que el motor registre con RegisterNatives en vez de convención de nombre)\n",
        "en": "(no `Java_*` exports found -- confirm by hand with objdump -T, the engine may register via RegisterNatives instead of the naming convention)\n",
        "pt": "(nenhum export `Java_*` encontrado -- confirmar manualmente com objdump -T, o motor pode registrar via RegisterNatives em vez da convenção de nomes)\n",
    },
    "init_port.plan_section4_title": {
        "es": "4. Checklist",
        "en": "4. Checklist",
        "pt": "4. Checklist",
    },
    "init_port.plan_check_repo": {
        "es": "Repo creado desde soloader-boilerplate, git init, .gitignore anti-DMCA.",
        "en": "Repo created from soloader-boilerplate, git init, anti-DMCA .gitignore.",
        "pt": "Repo criado a partir do soloader-boilerplate, git init, .gitignore anti-DMCA.",
    },
    "init_port.plan_check_decompiled": {
        "es": "APK decompilado (jadx) y .so decompilado(s) (Ghidra) -- ver sección 2/3.",
        "en": "APK decompiled (jadx) and .so file(s) decompiled (Ghidra) -- see section 2/3.",
        "pt": "APK decompilado (jadx) e .so decompilado(s) (Ghidra) -- ver seção 2/3.",
    },
    "init_port.plan_phases_intro": {
        "es": "Una fase por área. Marcar cada ítem solo cuando esté **confirmado en consola real** (no en teoría).\nLas fases 7, 9 y 12-14 incluyen los extras de la escena (ver ports de TheFlow, Rinnegatamante, v-atamanenko).",
        "en": "One phase per area. Tick an item only once it is **confirmed on real hardware** (not in theory).\nPhases 7, 9 and 12-14 include the scene extras (see ports by TheFlow, Rinnegatamante, v-atamanenko).",
        "pt": "Uma fase por área. Marcar cada item só quando estiver **confirmado em hardware real** (não na teoria).\nAs fases 7, 9 e 12-14 incluem os extras da cena (ver ports de TheFlow, Rinnegatamante, v-atamanenko).",
    },
    "init_port.plan_phase1_title": {
        "es": "Fase 1: Configuración y preparación",
        "en": "Phase 1: Setup and preparation",
        "pt": "Fase 1: Configuração e preparação",
    },
    "init_port.plan_phase2_title": {
        "es": "Fase 2: Decompilación",
        "en": "Phase 2: Decompilation",
        "pt": "Fase 2: Decompilação",
    },
    "init_port.plan_phase3": {
        "es": """### Fase 3: Análisis del motor real
- [ ] Identificar el motor por símbolos (`nm -D`/`objdump -T`), sin asumir "mismo motor que otro port".
- [ ] Mapear el ciclo de vida nativo (`JNI_OnLoad`, `nativeInit`, `onSurfaceCreated`, `nativeRender`, `onPause/onResume`) -- `psvita-toolkit jni-analyze`.
- [ ] Detectar middleware (FMOD, OpenAL, Bink, libpng, zlib, curl) y decidir stub vs. implementación.
- [ ] Decidir base: reuso de otro port de la misma familia (`sync-shared`) o boilerplate genérico.
- [ ] Revisar riesgos de alineación de memoria -- `psvita-toolkit align-check`.""",
        "en": """### Phase 3: Real engine analysis
- [ ] Identify the engine by symbols (`nm -D`/`objdump -T`), never assume "same engine as another port".
- [ ] Map the native lifecycle (`JNI_OnLoad`, `nativeInit`, `onSurfaceCreated`, `nativeRender`, `onPause/onResume`) -- `psvita-toolkit jni-analyze`.
- [ ] Detect middleware (FMOD, OpenAL, Bink, libpng, zlib, curl) and decide stub vs. implementation.
- [ ] Pick a base: reuse from a same-family port (`sync-shared`) or the generic boilerplate.
- [ ] Review memory-alignment risks -- `psvita-toolkit align-check`.""",
        "pt": """### Fase 3: Análise do motor real
- [ ] Identificar o motor por símbolos (`nm -D`/`objdump -T`), sem assumir "mesmo motor de outro port".
- [ ] Mapear o ciclo de vida nativo (`JNI_OnLoad`, `nativeInit`, `onSurfaceCreated`, `nativeRender`, `onPause/onResume`) -- `psvita-toolkit jni-analyze`.
- [ ] Detectar middleware (FMOD, OpenAL, Bink, libpng, zlib, curl) e decidir stub vs. implementação.
- [ ] Decidir a base: reuso de um port da mesma família (`sync-shared`) ou boilerplate genérico.
- [ ] Revisar riscos de alinhamento de memória -- `psvita-toolkit align-check`.""",
    },
    "init_port.plan_phase4": {
        "es": """### Fase 4: Bootstrap del loader y JNI
- [ ] `so_file_load` / `so_relocate` / `so_resolve` sin símbolos sin resolver críticos.
- [ ] Tabla de imports por defecto (libc, pthread, math) + stubs para lo no usado.
- [ ] FalsoJNI: registrar exports `Java_*` / `RegisterNatives` y callbacks hacia "Java" (paths, idioma, versión, toasts).
- [ ] Primer build (`psvita-toolkit build`) y deploy (`psvita-toolkit deploy`).
- [ ] Primer arranque en consola real: llega a `nativeInit` sin crash (si crashea: `psvita-toolkit analyze` + skill `so-crash-triage`).
- [ ] Logger conforme a la sección 6 desde el primer día.""",
        "en": """### Phase 4: Loader and JNI bootstrap
- [ ] `so_file_load` / `so_relocate` / `so_resolve` with no critical unresolved symbols.
- [ ] Default import table (libc, pthread, math) + stubs for what is unused.
- [ ] FalsoJNI: register `Java_*` exports / `RegisterNatives` and callbacks toward "Java" (paths, language, version, toasts).
- [ ] First build (`psvita-toolkit build`) and deploy (`psvita-toolkit deploy`).
- [ ] First boot on real hardware: reaches `nativeInit` without crashing (if it crashes: `psvita-toolkit analyze` + `so-crash-triage` skill).
- [ ] Logger compliant with section 6 from day one.""",
        "pt": """### Fase 4: Bootstrap do loader e JNI
- [ ] `so_file_load` / `so_relocate` / `so_resolve` sem símbolos críticos não resolvidos.
- [ ] Tabela de imports padrão (libc, pthread, math) + stubs para o que não é usado.
- [ ] FalsoJNI: registrar exports `Java_*` / `RegisterNatives` e callbacks para o "Java" (paths, idioma, versão, toasts).
- [ ] Primeiro build (`psvita-toolkit build`) e deploy (`psvita-toolkit deploy`).
- [ ] Primeira inicialização em hardware real: chega a `nativeInit` sem crash (se crashar: `psvita-toolkit analyze` + skill `so-crash-triage`).
- [ ] Logger conforme a seção 6 desde o primeiro dia.""",
    },
    "init_port.plan_phase5": {
        "es": """### Fase 5: Gráficos
- [ ] Inicializar vitaGL con memoria adecuada (`vglInitExtended`) y resolución 960x544.
- [ ] Wrappers GL según la versión GLES detectada (sección 1); stubs para extensiones no soportadas.
- [ ] Shaders: GLSL→CG o GXP precompilados (`psvita-toolkit shader-transpile`, `shader-live-reload`).
- [ ] Texturas: formatos comprimidos (ETC1/PVRTC/DXT) convertidos o soportados (`psvita-toolkit transcode-assets`).
- [ ] Escalado/aspect ratio correcto y sin artefactos en UI ni fuentes.""",
        "en": """### Phase 5: Graphics
- [ ] Initialize vitaGL with adequate memory (`vglInitExtended`) at 960x544.
- [ ] GL wrappers per the detected GLES version (section 1); stubs for unsupported extensions.
- [ ] Shaders: GLSL→CG or precompiled GXP (`psvita-toolkit shader-transpile`, `shader-live-reload`).
- [ ] Textures: compressed formats (ETC1/PVRTC/DXT) converted or supported (`psvita-toolkit transcode-assets`).
- [ ] Correct scaling/aspect ratio, no UI or font artifacts.""",
        "pt": """### Fase 5: Gráficos
- [ ] Inicializar o vitaGL com memória adequada (`vglInitExtended`) em 960x544.
- [ ] Wrappers GL conforme a versão GLES detectada (seção 1); stubs para extensões não suportadas.
- [ ] Shaders: GLSL→CG ou GXP pré-compilados (`psvita-toolkit shader-transpile`, `shader-live-reload`).
- [ ] Texturas: formatos comprimidos (ETC1/PVRTC/DXT) convertidos ou suportados (`psvita-toolkit transcode-assets`).
- [ ] Escala/aspect ratio corretos, sem artefatos na UI nem nas fontes.""",
    },
    "init_port.plan_phase6": {
        "es": """### Fase 6: Táctil
- [ ] Pantalla frontal → eventos touch del juego (multitouch, coordenadas escaladas a la resolución del motor).
- [ ] Ocultar o hacer opcional el gamepad virtual en pantalla cuando hay controles físicos.
- [ ] Panel trasero: decidir uso (L2/R2, ver Fase 7) o desactivarlo para evitar toques accidentales.""",
        "en": """### Phase 6: Touch
- [ ] Front screen → game touch events (multitouch, coordinates scaled to the engine resolution).
- [ ] Hide or make optional the on-screen virtual gamepad when physical controls exist.
- [ ] Rear panel: decide its use (L2/R2, see Phase 7) or disable it to avoid accidental touches.""",
        "pt": """### Fase 6: Toque
- [ ] Tela frontal → eventos touch do jogo (multitouch, coordenadas escaladas para a resolução do motor).
- [ ] Ocultar ou tornar opcional o gamepad virtual na tela quando há controles físicos.
- [ ] Painel traseiro: decidir o uso (L2/R2, ver Fase 7) ou desativá-lo para evitar toques acidentais.""",
    },
    "init_port.plan_phase7": {
        "es": """### Fase 7: Controles físicos
- [ ] Mapeo por defecto "Vita mejorado": D-pad, sticks, ✕○□△, L/R, START/SELECT con acción real en el juego.
- [ ] Stick analógico → movimiento/cámara (con aceleración si el juego lo espera).
- [ ] Deadzone interna (~8-12) y sensibilidad con defaults sanos.
- [ ] `controls.txt` remapeable en `<DATA_PATH>` (`VITA_BOTON = ACCION_JUEGO`); si falta, usar el mapeo por defecto.
- [ ] L2/R2 = panel trasero arriba, L3/R3 = frontal abajo (alternativa conmutable para PSTV).
- [ ] Ningún botón muerto: asignar acciones útiles (mapa, poción, guardado rápido).
- [ ] PSTV probado con DS3/DS4, o declarado en el README.
- [ ] Tabla de controles documentada en el README.""",
        "en": """### Phase 7: Physical controls
- [ ] "Enhanced Vita" default mapping: D-pad, sticks, ✕○□△, L/R, START/SELECT with a real in-game action.
- [ ] Analog stick → movement/camera (with acceleration if the game expects it).
- [ ] Inner deadzone (~8-12) and sensitivity with sane defaults.
- [ ] Remappable `controls.txt` in `<DATA_PATH>` (`VITA_BUTTON = GAME_ACTION`); if missing, use the default mapping.
- [ ] L2/R2 = rear panel top, L3/R3 = front bottom (toggleable alternative for PSTV).
- [ ] No dead buttons: assign useful actions (map, potion, quick save).
- [ ] PSTV tested with DS3/DS4, or stated in the README.
- [ ] Controls table documented in the README.""",
        "pt": """### Fase 7: Controles físicos
- [ ] Mapeamento padrão "Vita melhorado": D-pad, sticks, ✕○□△, L/R, START/SELECT com ação real no jogo.
- [ ] Stick analógico → movimento/câmera (com aceleração se o jogo esperar).
- [ ] Deadzone interna (~8-12) e sensibilidade com defaults sensatos.
- [ ] `controls.txt` remapeável em `<DATA_PATH>` (`BOTAO_VITA = ACAO_JOGO`); se faltar, usar o mapeamento padrão.
- [ ] L2/R2 = painel traseiro em cima, L3/R3 = frontal embaixo (alternativa alternável para PSTV).
- [ ] Nenhum botão morto: atribuir ações úteis (mapa, poção, salvamento rápido).
- [ ] PSTV testado com DS3/DS4, ou declarado no README.
- [ ] Tabela de controles documentada no README.""",
    },
    "init_port.plan_phase8": {
        "es": """### Fase 8: Audio
- [ ] Backend de audio (OpenSL ES / OpenAL / FMOD / AudioTrack vía JNI) mapeado a `sceAudioOut` o la lib del port.
- [ ] Música y efectos suenan sin crujidos ni desfase (sample rate y formato correctos).
- [ ] Pausa/reanudación del audio al suspender la consola o salir al LiveArea.
- [ ] Volumen de música/efectos respetado.""",
        "en": """### Phase 8: Audio
- [ ] Audio backend (OpenSL ES / OpenAL / FMOD / AudioTrack via JNI) mapped to `sceAudioOut` or the port's lib.
- [ ] Music and SFX play without crackling or drift (correct sample rate and format).
- [ ] Audio pauses/resumes on console suspend or return to LiveArea.
- [ ] Music/SFX volume settings respected.""",
        "pt": """### Fase 8: Áudio
- [ ] Backend de áudio (OpenSL ES / OpenAL / FMOD / AudioTrack via JNI) mapeado para `sceAudioOut` ou a lib do port.
- [ ] Música e efeitos tocam sem estalos nem dessincronia (sample rate e formato corretos).
- [ ] Pausa/retomada do áudio ao suspender o console ou voltar ao LiveArea.
- [ ] Volume de música/efeitos respeitado.""",
    },
    "init_port.plan_phase9": {
        "es": """### Fase 9: Videos / FMV
- [ ] Inventariar videos del APK/OBB (intro, cinemáticas, créditos) y su formato.
- [ ] Reproducir con el player de Vita (`sceAvPlayer`) o transcodificar a MP4 H.264 compatible.
- [ ] Script de transcodificación opcional (ffmpeg `.sh`/`.bat`) para el jugador.
- [ ] Skip con botón y **skip elegante si el video falta** (nunca colgar ni pantalla negra).
- [ ] Audio del video sincronizado y restaurado al volver al juego.""",
        "en": """### Phase 9: Videos / FMV
- [ ] Inventory the APK/OBB videos (intro, cutscenes, credits) and their format.
- [ ] Play with the Vita player (`sceAvPlayer`) or transcode to compatible H.264 MP4.
- [ ] Optional transcode script (ffmpeg `.sh`/`.bat`) for the player.
- [ ] Button skip and a **graceful skip when the video is missing** (never hang or black screen).
- [ ] Video audio in sync and restored when returning to the game.""",
        "pt": """### Fase 9: Vídeos / FMV
- [ ] Inventariar os vídeos do APK/OBB (intro, cinemáticas, créditos) e seu formato.
- [ ] Reproduzir com o player do Vita (`sceAvPlayer`) ou transcodificar para MP4 H.264 compatível.
- [ ] Script de transcodificação opcional (ffmpeg `.sh`/`.bat`) para o jogador.
- [ ] Skip por botão e **skip elegante se o vídeo faltar** (nunca travar nem tela preta).
- [ ] Áudio do vídeo sincronizado e restaurado ao voltar ao jogo.""",
    },
    "init_port.plan_phase10": {
        "es": """### Fase 10: Assets, datos, idioma y saves
- [ ] Redirigir rutas Android (`/sdcard`, `getFilesDir`, OBB) a `<DATA_PATH>`.
- [ ] Checker al arrancar: si falta el `.so` o los assets, diálogo con la ruta exacta (no crash mudo).
- [ ] `datafiles.zip` separado del VPK para assets convertibles (texturas, shaders).
- [ ] Idioma = idioma del sistema (`sceAppUtilSystemParamGetById`) con fallback a inglés.
- [ ] Saves en `<DATA_PATH>saves/`; documentar cómo importar un save de Android.
- [ ] Guardado rápido al salir + reanudar el último save (si el juego lo permite).""",
        "en": """### Phase 10: Assets, data, language and saves
- [ ] Redirect Android paths (`/sdcard`, `getFilesDir`, OBB) to `<DATA_PATH>`.
- [ ] Boot checker: if the `.so` or assets are missing, show a dialog with the exact path (no silent crash).
- [ ] `datafiles.zip` separate from the VPK for convertible assets (textures, shaders).
- [ ] Language = system language (`sceAppUtilSystemParamGetById`) with English fallback.
- [ ] Saves in `<DATA_PATH>saves/`; document how to import an Android save.
- [ ] Quick save on exit + resume last save (if the game allows it).""",
        "pt": """### Fase 10: Assets, dados, idioma e saves
- [ ] Redirecionar caminhos Android (`/sdcard`, `getFilesDir`, OBB) para `<DATA_PATH>`.
- [ ] Checker na inicialização: se faltar o `.so` ou os assets, diálogo com o caminho exato (sem crash mudo).
- [ ] `datafiles.zip` separado do VPK para assets conversíveis (texturas, shaders).
- [ ] Idioma = idioma do sistema (`sceAppUtilSystemParamGetById`) com fallback para inglês.
- [ ] Saves em `<DATA_PATH>saves/`; documentar como importar um save do Android.
- [ ] Salvamento rápido ao sair + retomar o último save (se o jogo permitir).""",
    },
    "init_port.plan_phase11": {
        "es": """### Fase 11: Rendimiento
- [ ] Relojes 444/222/222/166 al arrancar + nota "PSVshell a 500 MHz opcional" en el README.
- [ ] Cache de shaders en disco (primer arranque lento, siguientes rápidos) -- documentado.
- [ ] FPS estable medido con frame time (`psvita-toolkit perf-telemetry`), sin prometer 60 FPS que el motor no da.
- [ ] Memoria bajo control (`psvita-toolkit mem-profile`) y sesión larga sin fugas (`psvita-toolkit soak-test`).""",
        "en": """### Phase 11: Performance
- [ ] 444/222/222/166 clocks at boot + "optional PSVshell at 500 MHz" note in the README.
- [ ] On-disk shader cache (slow first boot, fast afterwards) -- documented.
- [ ] Stable FPS measured by frame time (`psvita-toolkit perf-telemetry`), never promising 60 FPS the engine can't do.
- [ ] Memory under control (`psvita-toolkit mem-profile`) and a long session without leaks (`psvita-toolkit soak-test`).""",
        "pt": """### Fase 11: Desempenho
- [ ] Clocks 444/222/222/166 na inicialização + nota "PSVshell a 500 MHz opcional" no README.
- [ ] Cache de shaders em disco (primeira inicialização lenta, seguintes rápidas) -- documentado.
- [ ] FPS estável medido por frame time (`psvita-toolkit perf-telemetry`), sem prometer 60 FPS que o motor não entrega.
- [ ] Memória sob controle (`psvita-toolkit mem-profile`) e sessão longa sem vazamentos (`psvita-toolkit soak-test`).""",
    },
    "init_port.plan_phase12": {
        "es": """### Fase 12: Configurador y opciones
- [ ] `settings.bin` persistente (`source/utils/settings.c` del boilerplate).
- [ ] App configuradora lanzada con el botón **Settings** del LiveArea (redirect `-config`).
- [ ] Opciones mínimas: deadzones, sensibilidad, FPS lock 30/60/off, overlay táctil on/off.
- [ ] Opciones gráficas en runtime (detalle, filtro bilinear/nítido) en vez de flags de build.""",
        "en": """### Phase 12: Configurator and options
- [ ] Persistent `settings.bin` (boilerplate's `source/utils/settings.c`).
- [ ] Configurator app launched from the LiveArea **Settings** button (`-config` redirect).
- [ ] Minimum options: deadzones, sensitivity, FPS lock 30/60/off, touch overlay on/off.
- [ ] Runtime graphics options (detail, bilinear/sharp filter) instead of build flags.""",
        "pt": """### Fase 12: Configurador e opções
- [ ] `settings.bin` persistente (`source/utils/settings.c` do boilerplate).
- [ ] App configurador aberto pelo botão **Settings** do LiveArea (redirect `-config`).
- [ ] Opções mínimas: deadzones, sensibilidade, FPS lock 30/60/off, overlay de toque on/off.
- [ ] Opções gráficas em runtime (detalhe, filtro bilinear/nítido) em vez de flags de build.""",
    },
    "init_port.plan_phase13": {
        "es": """### Fase 13: Trofeos
- [ ] Lista corta y alcanzable (arranque, primer jefe, fin de capítulo, 100%); nada imposible por bugs conocidos.
- [ ] `sce_sys/trophy/` con TRP + iconos hi-res, algunos ocultos si el juego original los tenía.
- [ ] Desbloqueo con llamada real (`sceNpTrophyUnlock`) enganchada a los eventos del juego, no flag local.
- [ ] Probado en consola: el trofeo aparece y queda registrado.""",
        "en": """### Phase 13: Trophies
- [ ] Short, achievable list (boot, first boss, chapter end, 100%); nothing impossible due to known bugs.
- [ ] `sce_sys/trophy/` with TRP + hi-res icons, some hidden if the original game had them.
- [ ] Unlock via a real call (`sceNpTrophyUnlock`) hooked to game events, not a local flag.
- [ ] Tested on console: the trophy pops and is recorded.""",
        "pt": """### Fase 13: Troféus
- [ ] Lista curta e alcançável (início, primeiro chefe, fim de capítulo, 100%); nada impossível por bugs conhecidos.
- [ ] `sce_sys/trophy/` com TRP + ícones hi-res, alguns ocultos se o jogo original tinha.
- [ ] Desbloqueio com chamada real (`sceNpTrophyUnlock`) ligada aos eventos do jogo, não flag local.
- [ ] Testado no console: o troféu aparece e fica registrado.""",
    },
    "init_port.plan_phase14": {
        "es": """### Fase 14: LiveArea y VPK
- [ ] `icon0.png` 128x128, `bg.png` 840x500, `startup.png` 280x158 (8 bits indexados) -- `psvita-toolkit livearea --validate`.
- [ ] `template.xml` con frames + botón Settings/Config.
- [ ] Manual en `sce_sys/manual/` y música de fondo opcional.
- [ ] `param.sfo` con Title ID `{titleid}`, título y versión correctos.
- [ ] VPK instala sin el warning `0x8010113D`.""",
        "en": """### Phase 14: LiveArea and VPK
- [ ] `icon0.png` 128x128, `bg.png` 840x500, `startup.png` 280x158 (8-bit indexed) -- `psvita-toolkit livearea --validate`.
- [ ] `template.xml` with frames + Settings/Config button.
- [ ] Manual in `sce_sys/manual/` and optional background music.
- [ ] `param.sfo` with Title ID `{titleid}`, correct title and version.
- [ ] VPK installs without the `0x8010113D` warning.""",
        "pt": """### Fase 14: LiveArea e VPK
- [ ] `icon0.png` 128x128, `bg.png` 840x500, `startup.png` 280x158 (8 bits indexados) -- `psvita-toolkit livearea --validate`.
- [ ] `template.xml` com frames + botão Settings/Config.
- [ ] Manual em `sce_sys/manual/` e música de fundo opcional.
- [ ] `param.sfo` com Title ID `{titleid}`, título e versão corretos.
- [ ] VPK instala sem o aviso `0x8010113D`.""",
    },
    "init_port.plan_phase15": {
        "es": """### Fase 15: Pruebas en hardware real y release
- [ ] Partida completa (o hasta donde llegue) sin crashes; suspender/reanudar la consola sin romper el juego.
- [ ] Vita 1000/2000 y PSTV (o limitación declarada).
- [ ] Build de producción: `PORT_TRACE=OFF` o `psvita-toolkit log-trace --remove`.
- [ ] Parches por firma, no por offset fijo; versiones de APK probadas listadas en el README.
- [ ] Release: VPK + `gamefiles.txt` + changelog + screenshots + requisitos (kubridge, FdFix, libshacccg).""",
        "en": """### Phase 15: Real-hardware testing and release
- [ ] Full playthrough (or as far as it goes) without crashes; console suspend/resume without breaking the game.
- [ ] Vita 1000/2000 and PSTV (or a stated limitation).
- [ ] Production build: `PORT_TRACE=OFF` or `psvita-toolkit log-trace --remove`.
- [ ] Signature-based patches, not fixed offsets; tested APK versions listed in the README.
- [ ] Release: VPK + `gamefiles.txt` + changelog + screenshots + requirements (kubridge, FdFix, libshacccg).""",
        "pt": """### Fase 15: Testes em hardware real e release
- [ ] Jogo completo (ou até onde chegar) sem crashes; suspender/retomar o console sem quebrar o jogo.
- [ ] Vita 1000/2000 e PSTV (ou limitação declarada).
- [ ] Build de produção: `PORT_TRACE=OFF` ou `psvita-toolkit log-trace --remove`.
- [ ] Patches por assinatura, não por offset fixo; versões de APK testadas listadas no README.
- [ ] Release: VPK + `gamefiles.txt` + changelog + screenshots + requisitos (kubridge, FdFix, libshacccg).""",
    },
    "init_port.plan_section5_title": {
        "es": "5. Herramientas",
        "en": "5. Tools",
        "pt": "5. Ferramentas",
    },
    "init_port.plan_tools_text": {
        "es": "Este port se gestiona con **psvita-port-toolkit** (standalone, fuera de este repo). Desde el\ntoolkit: `Continuar con un port existente` → elegí esta carpeta (ya tiene `.psvita-toolkit.json`).",
        "en": "This port is managed with **psvita-port-toolkit** (standalone, outside this repo). From the\ntoolkit: `Continue with an existing port` → pick this folder (it already has `.psvita-toolkit.json`).",
        "pt": "Este port é gerenciado com **psvita-port-toolkit** (standalone, fora deste repo). A partir da\nferramenta: `Continuar com um port existente` → escolha esta pasta (já tem `.psvita-toolkit.json`).",
    },
    "init_port.plan_section6_title": {
        "es": "6. Estándar de logs en consola",
        "en": "6. On-console log standard",
        "pt": "6. Padrão de logs no console",
    },
    "init_port.plan_log_text": {
        "es": "El log del juego vive en `<DATA_PATH>logs/<slug>_NNN.log` (`<slug>` = {slug}), incremental de\n001 a 999 con `next.idx` (nunca `log_<timestamp>.txt`, nunca base 000, nunca ruta hardcodeada:\nusar `DATA_PATH`). La subida FTP crea `logs/` y `saves/` en la consola aunque estén vacíos.\nComandos del toolkit (sin IA, deterministas): `psvita-toolkit log-standard --fix-dirs` (audita\ny crea los dirs), `psvita-toolkit log-trace --ensure-flag` (inyecta trazas `[TRACE] >> f()` por\nfunción bajo `#ifdef PORT_TRACE`; compilar con `-DPORT_TRACE=ON` para debug, `OFF` para\nproducción, o `psvita-toolkit log-trace --remove` para quitarlas del árbol).",
        "en": "The game log lives at `<DATA_PATH>logs/<slug>_NNN.log` (`<slug>` = {slug}), incrementing\n001 to 999 with `next.idx` (never `log_<timestamp>.txt`, never 000-based, never a hardcoded\npath: use `DATA_PATH`). The FTP upload creates `logs/` and `saves/` on the console even when\nempty. Toolkit commands (no AI, deterministic): `psvita-toolkit log-standard --fix-dirs` (audits\nand creates the dirs), `psvita-toolkit log-trace --ensure-flag` (injects per-function\n`[TRACE] >> f()` traces under `#ifdef PORT_TRACE`; build with `-DPORT_TRACE=ON` for debug,\n`OFF` for production, or `psvita-toolkit log-trace --remove` to strip them from the tree).",
        "pt": "O log do jogo fica em `<DATA_PATH>logs/<slug>_NNN.log` (`<slug>` = {slug}), incremental de\n001 a 999 com `next.idx` (nunca `log_<timestamp>.txt`, nunca base 000, nunca caminho hardcoded:\nusar `DATA_PATH`). O upload FTP cria `logs/` e `saves/` no console mesmo vazios.\nComandos do toolkit (sem IA, determinísticos): `psvita-toolkit log-standard --fix-dirs` (audita\ne cria os dirs), `psvita-toolkit log-trace --ensure-flag` (injeta traces `[TRACE] >> f()` por\nfunção sob `#ifdef PORT_TRACE`; compilar com `-DPORT_TRACE=ON` para debug, `OFF` para\nprodução, ou `psvita-toolkit log-trace --remove` para removê-los da árvore).",
    },
    "init_port.plan_written": {
        "es": "[+] PORTING_PLAN.md escrito.",
        "en": "[+] PORTING_PLAN.md written.",
        "pt": "[+] PORTING_PLAN.md escrito.",
    },
    "init_port.progress_title": {
        "es": "Registro de Progreso — {game_name} (PS Vita)",
        "en": "Progress Log — {game_name} (PS Vita)",
        "pt": "Registro de Progresso — {game_name} (PS Vita)",
    },
    "init_port.progress_phase1_title": {
        "es": "Fase 1: Configuración y Preparación (Completada — {today})",
        "en": "Phase 1: Setup and Preparation (Completed — {today})",
        "pt": "Fase 1: Configuração e Preparação (Concluída — {today})",
    },
    "init_port.progress_p1_repo": {
        "es": "Repo creado desde soloader-boilerplate, `.gitignore` anti-DMCA.",
        "en": "Repo created from soloader-boilerplate, anti-DMCA `.gitignore`.",
        "pt": "Repo criado a partir do soloader-boilerplate, `.gitignore` anti-DMCA.",
    },
    "init_port.progress_p1_apk": {
        "es": "APK `{apk}` copiado y extraído.",
        "en": "APK `{apk}` copied and extracted.",
        "pt": "APK `{apk}` copiado e extraído.",
    },
    "init_port.progress_p1_abi": {
        "es": "ABI detectada: {abis} (elegida: {preferred}).",
        "en": "Detected ABI: {abis} (chosen: {preferred}).",
        "pt": "ABI detectada: {abis} (escolhida: {preferred}).",
    },
    "init_port.progress_p1_gles": {
        "es": "GLES detectado: {gles}",
        "en": "GLES detected: {gles}",
        "pt": "GLES detectado: {gles}",
    },
    "init_port.progress_phase2_title": {
        "es": "Fase 2: Decompilación (Completada — {today})",
        "en": "Phase 2: Decompilation (Completed — {today})",
        "pt": "Fase 2: Decompilação (Concluída — {today})",
    },
    "init_port.progress_p2_jadx_done": {
        "es": "jadx: corrido, resultados en decompiled/apk_jadx/.",
        "en": "jadx: ran, results in decompiled/apk_jadx/.",
        "pt": "jadx: executado, resultados em decompiled/apk_jadx/.",
    },
    "init_port.progress_p2_jadx_pending": {
        "es": "jadx: NO corrido -- pendiente.",
        "en": "jadx: NOT run -- pending.",
        "pt": "jadx: NÃO executado -- pendente.",
    },
    "init_port.progress_p2_ghidra_done": {
        "es": "Ghidra (.so): corrido para cada .so.",
        "en": "Ghidra (.so): ran for each .so.",
        "pt": "Ghidra (.so): executado para cada .so.",
    },
    "init_port.progress_p2_ghidra_pending": {
        "es": "Ghidra (.so): NO corrido (docker/imagen no disponibles) -- pendiente.",
        "en": "Ghidra (.so): NOT run (docker/image not available) -- pending.",
        "pt": "Ghidra (.so): NÃO executado (docker/imagem não disponíveis) -- pendente.",
    },
    "init_port.progress_phase3_title": {
        "es": "Fase 3: Análisis del Motor Real (Pendiente)",
        "en": "Phase 3: Real Engine Analysis (Pending)",
        "pt": "Fase 3: Análise do Motor Real (Pendente)",
    },
    "init_port.progress_p3_confirm_engine": {
        "es": "Confirmar si comparte motor con algún port hermano.",
        "en": "Confirm whether it shares an engine with any sibling port.",
        "pt": "Confirmar se compartilha o motor com algum port irmão.",
    },
    "init_port.progress_p3_read_sources": {
        "es": "Leer decompiled/apk_jadx/sources/ para el ciclo de vida nativo real.",
        "en": "Read decompiled/apk_jadx/sources/ for the real native lifecycle.",
        "pt": "Ler decompiled/apk_jadx/sources/ para o ciclo de vida nativo real.",
    },
    "init_port.progress_p3_confirm_jni": {
        "es": "Confirmar exports JNI reales y si hay RegisterNatives.",
        "en": "Confirm the real JNI exports and whether RegisterNatives is used.",
        "pt": "Confirmar os exports JNI reais e se há RegisterNatives.",
    },
    "init_port.progress_phase4_title": {
        "es": "Fase 4 en adelante: Pendiente",
        "en": "Phase 4 onward: Pending",
        "pt": "Fase 4 em diante: Pendente",
    },
    "init_port.progress_see_plan": {
        "es": "Ver PORTING_PLAN.md sección 4. Actualizar con un bug confirmado a la vez en pruebas reales.",
        "en": "See PORTING_PLAN.md section 4. Update with one confirmed bug at a time from real testing.",
        "pt": "Ver PORTING_PLAN.md seção 4. Atualizar com um bug confirmado por vez em testes reais.",
    },
    "init_port.progress_written": {
        "es": "[+] port_progress.md escrito.",
        "en": "[+] port_progress.md written.",
        "pt": "[+] port_progress.md escrito.",
    },
    "init_port.skill_copied": {
        "es": "[+] Skill '{skill}' copiada al repo.",
        "en": "[+] Skill '{skill}' copied to the repo.",
        "pt": "[+] Skill '{skill}' copiada para o repo.",
    },
    "init_port.skill_not_found": {
        "es": "[!] Skill '{skill}' no encontrada en {source} -- se omite.",
        "en": "[!] Skill '{skill}' not found in {source} -- skipping.",
        "pt": "[!] Skill '{skill}' não encontrada em {source} -- ignorada.",
    },
    "init_port.claude_md_title": {
        "es": "{game_name} — Port a PS Vita",
        "en": "{game_name} — PS Vita Port",
        "pt": "{game_name} — Port para PS Vita",
    },
    "init_port.claude_md_intro": {
        "es": "Port de `{apk}` (Android) a PS Vita vía soloader. Generado con **psvita-port-toolkit**.",
        "en": "Port of `{apk}` (Android) to PS Vita via soloader. Generated with **psvita-port-toolkit**.",
        "pt": "Port de `{apk}` (Android) para PS Vita via soloader. Gerado com **psvita-port-toolkit**.",
    },
    "init_port.claude_md_structure_title": {
        "es": "Estructura",
        "en": "Structure",
        "pt": "Estrutura",
    },
    "init_port.claude_md_struct_extract": {
        "es": "APK extraído (gitignored).",
        "en": "Extracted APK (gitignored).",
        "pt": "APK extraído (gitignored).",
    },
    "init_port.claude_md_struct_decompiled": {
        "es": "Java (jadx) y pseudo-C (Ghidra) del/los .so (gitignored, regenerable).",
        "en": "Java (jadx) and pseudo-C (Ghidra) from the .so file(s) (gitignored, regenerable).",
        "pt": "Java (jadx) e pseudo-C (Ghidra) do(s) .so (gitignored, regenerável).",
    },
    "init_port.claude_md_struct_scaffold": {
        "es": "scaffold del boilerplate (SoLoader + FalsoJNI).",
        "en": "boilerplate scaffold (SoLoader + FalsoJNI).",
        "pt": "scaffold do boilerplate (SoLoader + FalsoJNI).",
    },
    "init_port.claude_md_struct_plan": {
        "es": "plan vivo, actualizar a medida que se confirman cosas del motor real.",
        "en": "living plan, update as things about the real engine get confirmed.",
        "pt": "plano vivo, atualizar à medida que as coisas do motor real forem confirmadas.",
    },
    "init_port.claude_md_struct_progress": {
        "es": "bitácora, un bug confirmado a la vez.",
        "en": "log, one confirmed bug at a time.",
        "pt": "diário, um bug confirmado por vez.",
    },
    "init_port.claude_md_struct_config": {
        "es": "config para el toolkit standalone (build/deploy/logs/LiveArea/crash dumps).",
        "en": "config for the standalone toolkit (build/deploy/logs/LiveArea/crash dumps).",
        "pt": "config para a ferramenta standalone (build/deploy/logs/LiveArea/crash dumps).",
    },
    "init_port.claude_md_no_porting_tools": {
        "es": 'Este port **no** tiene una copia local de `porting_tools/` -- todo el build/deploy/debug se maneja\ndesde **psvita-port-toolkit**, la herramienta standalone (fuera de este repo). Abrí el toolkit y\nelegí "Continuar con un port existente" apuntando a esta carpeta.',
        "en": 'This port does **not** have a local copy of `porting_tools/` -- all build/deploy/debug is handled\nfrom **psvita-port-toolkit**, the standalone tool (outside this repo). Open the toolkit and\nchoose "Continue with an existing port" pointing at this folder.',
        "pt": 'Este port **não** tem uma cópia local de `porting_tools/` -- todo o build/deploy/debug é gerenciado\npela **psvita-port-toolkit**, a ferramenta standalone (fora deste repo). Abra a ferramenta e\nescolha "Continuar com um port existente" apontando para esta pasta.',
    },
    "init_port.claude_md_findings_title": {
        "es": "Hallazgos de motor (automáticos, sin confirmar)",
        "en": "Engine findings (automatic, unconfirmed)",
        "pt": "Achados do motor (automáticos, não confirmados)",
    },
    "init_port.claude_md_finding_abi": {
        "es": "ABI: {abis} (preferida: {preferred})",
        "en": "ABI: {abis} (preferred: {preferred})",
        "pt": "ABI: {abis} (preferida: {preferred})",
    },
    "init_port.claude_md_finding_gles": {
        "es": "GLES: {gles}",
        "en": "GLES: {gles}",
        "pt": "GLES: {gles}",
    },
    "init_port.claude_md_finding_package": {
        "es": "Paquete Java: {package}",
        "en": "Java package: {package}",
        "pt": "Pacote Java: {package}",
    },
    "init_port.pending": {
        "es": "pendiente",
        "en": "pending",
        "pt": "pendente",
    },
    "init_port.claude_md_workflow_title": {
        "es": "Flujo de trabajo esperado",
        "en": "Expected workflow",
        "pt": "Fluxo de trabalho esperado",
    },
    "init_port.claude_md_wf1": {
        "es": "Análisis de símbolos antes de tocar loader/source -- skill `psvita-port-init` cubrió la Fase 0-2.",
        "en": "Symbol analysis before touching loader/source -- the `psvita-port-init` skill covered Phase 0-2.",
        "pt": "Análise de símbolos antes de mexer em loader/source -- a skill `psvita-port-init` cobriu a Fase 0-2.",
    },
    "init_port.claude_md_wf2": {
        "es": "Bootstrap del loader guiado por la skill `psvita-porting`.",
        "en": "Loader bootstrap guided by the `psvita-porting` skill.",
        "pt": "Bootstrap do loader guiado pela skill `psvita-porting`.",
    },
    "init_port.claude_md_wf3": {
        "es": "Build/deploy con el toolkit standalone → probar en consola real.",
        "en": "Build/deploy with the standalone toolkit → test on real hardware.",
        "pt": "Build/deploy com a ferramenta standalone → testar em hardware real.",
    },
    "init_port.claude_md_wf4": {
        "es": "Un bug a la vez, guiado por el log real -- skill `so-crash-triage`.",
        "en": "One bug at a time, guided by the real log -- the `so-crash-triage` skill.",
        "pt": "Um bug por vez, guiado pelo log real -- skill `so-crash-triage`.",
    },
    "init_port.claude_md_wf5": {
        "es": "Actualizar `port_progress.md` con cada bug confirmado.",
        "en": "Update `port_progress.md` with each confirmed bug.",
        "pt": "Atualizar `port_progress.md` a cada bug confirmado.",
    },
    "init_port.claude_md_written": {
        "es": "[+] CLAUDE.md escrito.",
        "en": "[+] CLAUDE.md written.",
        "pt": "[+] CLAUDE.md escrito.",
    },
    "init_port.project_config_written": {
        "es": "[+] {new_dir}/.psvita-toolkit.json escrito -- el toolkit ya reconoce este port.",
        "en": "[+] {new_dir}/.psvita-toolkit.json written -- the toolkit now recognizes this port.",
        "pt": "[+] {new_dir}/.psvita-toolkit.json escrito -- a ferramenta já reconhece este port.",
    },
    "init_port.creating_port_title": {
        "es": "Creando port: {game_name}",
        "en": "Creating port: {game_name}",
        "pt": "Criando port: {game_name}",
    },
    "init_port.wizard_done": {
        "es": "Listo: {new_dir}",
        "en": "Done: {new_dir}",
        "pt": "Pronto: {new_dir}",
    },
    "init_port.next_step": {
        "es": "Siguiente paso: seguir PORTING_PLAN.md sección 4 (empezando por la Fase 3,\nanálisis real del motor) antes de escribir código en source/.",
        "en": "Next step: follow PORTING_PLAN.md section 4 (starting with Phase 3,\nreal engine analysis) before writing any code in source/.",
        "pt": "Próximo passo: seguir PORTING_PLAN.md seção 4 (começando pela Fase 3,\nanálise real do motor) antes de escrever código em source/.",
    },
}
i18n.register(STRINGS)


def _sh(cmd, cwd=None, check=True, capture=False):
    return subprocess.run(cmd, cwd=cwd, check=check,
                           capture_output=capture, text=True)


def _have(cmd):
    return shutil.which(cmd) is not None


def _same_file(a, b):
    """!
    @brief Check whether `a` and `b` refer to the same file on disk.
    @param a First path.
    @param b Second path.
    @return `True` if both paths resolve to the same inode/device.
    @note Uses `os.path.samefile()` (inode/device comparison) rather than
          comparing `Path.resolve()` as strings, so it's correct even on a
          case-insensitive filesystem. See `docs/dev-notes/init_port.md`.
    """
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _merge_tree_no_clobber(src, dst):
    """!
    @brief Recursively copy `src` into `dst` without overwriting files that
           already exist in `dst`.
    @param src Source directory tree.
    @param dst Destination directory (created as needed).
    @note Equivalent to `cp -Rn src/. dst/`, but without a bug affecting
          macOS's BSD `cp` in this exact usage -- see
          `docs/dev-notes/init_port.md` for why a shell `cp -Rn` was replaced
          with this pure-Python walk.
    """
    src, dst = Path(src), Path(dst)
    for item in sorted(src.rglob("*")):
        target = dst / item.relative_to(src)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _have_docker_image(image):
    if not _have("docker"):
        return False
    r = subprocess.run(["docker", "image", "inspect", image],
                        capture_output=True, text=True)
    return r.returncode == 0


def check_prereqs(global_cfg):
    print(f"{C.BOLD}{t('init_port.checking_prereqs')}{C.RESET}")
    have_jadx = _have("jadx")
    jadx_status = t("init_port.jadx_found") if have_jadx else t("init_port.jadx_missing")
    print(f"  {'[+]' if have_jadx else '[!]'} jadx {jadx_status}")

    have_docker_so = _have_docker_image("devrvk/so-decompiler")
    if _have("docker"):
        docker_status = t("init_port.docker_so_found") if have_docker_so else t("init_port.docker_so_missing")
        print(f"  {'[+]' if have_docker_so else '[!]'} docker + devrvk/so-decompiler {docker_status}")
    else:
        print(f"  [!] {t('init_port.docker_not_found')}")

    for tool in ("git", "unzip"):
        if not _have(tool):
            raise RuntimeError(t("init_port.tool_not_installed", tool=tool))

    boilerplate_dir = Path(global_cfg["boilerplate_dir"])
    if not boilerplate_dir.is_dir():
        raise RuntimeError(t("init_port.boilerplate_not_found", path=boilerplate_dir))

    return have_jadx, have_docker_so


def _default_slug(game_name):
    return "".join(c for c in game_name.lower() if c.isalnum())


def _used_titleids(base_dir):
    used = set()
    base = Path(base_dir)
    if not base.is_dir():
        return used
    for cmake in base.glob("*/CMakeLists.txt"):
        try:
            text = cmake.read_text(errors="ignore")
        except OSError:
            continue
        m = re.search(r'VITA_TITLEID\s+"([A-Za-z0-9]{9})"', text)
        if m:
            used.add(m.group(1))
    return used


def _own_titleid(project_dir):
    """!
    @brief Get the TITLEID this project directory already has, if any.
    @param project_dir Path to the target project directory.
    @return The 9-character TITLEID already assigned to `project_dir` (from a
            previous attempt), or `None` if it has none yet or still has the
            boilerplate's placeholder (`"SOLOADER0"`).
    @note Excluded from the collision check in `prompt_inputs()` -- reusing a
          project's OWN TITLEID isn't a collision with another port, it's
          resuming the same one. See `docs/dev-notes/init_port.md`.
    """
    cmake = Path(project_dir) / "CMakeLists.txt"
    if not cmake.exists():
        return None
    try:
        text = cmake.read_text(errors="ignore")
    except OSError:
        return None
    m = re.search(r'VITA_TITLEID\s+"([A-Za-z0-9]{9})"', text)
    return m.group(1) if m and m.group(1) != "SOLOADER0" else None


def prompt_inputs(global_cfg):
    tui.clear()
    tui.print_banner(t("init_port.wizard_title"))

    game_name = input(f"{C.BOLD}{t('init_port.game_name_prompt')}{C.RESET}\n> ").strip()
    if not game_name:
        raise RuntimeError(t("init_port.game_name_required"))

    default_slug = _default_slug(game_name)
    slug = input(f"{C.BOLD}{t('init_port.slug_prompt')}{C.RESET} [{default_slug}]: ").strip() or default_slug

    default_folder = game_name.replace(" ", "-") + "-vita"
    folder_name = input(f"{C.BOLD}{t('init_port.folder_prompt')}{C.RESET} [{default_folder}]: ").strip() or default_folder

    project_name = slug.replace("-", "_")

    apk_path = tui.input_path(t("init_port.apk_path_prompt"), must_exist=True)

    vita_ip = input(f"{C.BOLD}{t('init_port.vita_ip_prompt')}{C.RESET} [192.168.1.100]: ").strip() or "192.168.1.100"

    base_dir = Path(global_cfg["base_dir"])
    new_dir = base_dir / folder_name
    own_id = _own_titleid(new_dir)

    used_ids = _used_titleids(base_dir)
    if own_id:
        used_ids.discard(own_id)

    print(f"\n{C.DIM}{t('init_port.titleids_used_header', base_dir=base_dir)}{C.RESET}")
    for tid in sorted(used_ids):
        print(f"    {tid}")
    if own_id:
        print(f"{C.YELLOW}{t('init_port.own_titleid_notice', new_dir=new_dir, own_id=own_id)}{C.RESET}")

    while True:
        prompt = f"{C.BOLD}{t('init_port.titleid_prompt')}{C.RESET}"
        prompt += f" [{own_id}]" if own_id else ""
        titleid = input(f"{prompt}: ").strip().upper() or (own_id or "")
        if len(titleid) != 9:
            print(f"{C.RED}{t('init_port.titleid_length_error')}{C.RESET}")
            continue
        if titleid in used_ids:
            print(f"{C.RED}{t('init_port.titleid_in_use')}{C.RESET}")
            continue
        break

    if new_dir.exists():
        print(f"{C.YELLOW}{t('init_port.dir_exists_reuse', new_dir=new_dir)}{C.RESET}")

    print(f"\n{C.BOLD}{t('init_port.summary_title')}{C.RESET}")
    print(t("init_port.summary_game", name=game_name))
    print(t("init_port.summary_slug", slug=slug))
    print(t("init_port.summary_folder", folder=new_dir))
    print(t("init_port.summary_project", project=project_name))
    print(t("init_port.summary_apk", apk=apk_path))
    print(t("init_port.summary_titleid", titleid=titleid))
    print(t("init_port.summary_vita_ip", ip=vita_ip))

    if not tui.confirm(f"\n{t('init_port.confirm_continue')}"):
        raise RuntimeError(t("init_port.cancelled_by_user"))

    skip_ghidra = not tui.confirm(f"\n{t('init_port.prompt_skip_ghidra')}", default=True)

    return {
        "game_name": game_name, "slug": slug, "folder_name": folder_name,
        "project_name": project_name, "apk_path": apk_path,
        "vita_ip": vita_ip, "titleid": titleid, "new_dir": new_dir,
        "skip_ghidra": skip_ghidra,
    }


def setup_repo_dir(global_cfg, ctx):
    new_dir = ctx["new_dir"]
    boilerplate_dir = Path(global_cfg["boilerplate_dir"])

    if (new_dir / ".git").is_dir():
        print(f"{C.YELLOW}{t('init_port.already_git_repo', new_dir=new_dir)}{C.RESET}")
        return

    if new_dir.exists() and any(new_dir.iterdir()):
        print(t("init_port.dir_exists_merge", new_dir=new_dir))
    else:
        print(t("init_port.cloning_boilerplate", new_dir=new_dir))

    with tempfile.TemporaryDirectory() as tmp_clone:
        _sh(["git", "clone", "--quiet", str(boilerplate_dir), tmp_clone])
        print(t("init_port.init_submodule"))
        r = subprocess.run(["git", "submodule", "update", "--init", "--recursive"],
                            cwd=tmp_clone, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"{C.YELLOW}{t('init_port.submodule_failed')}{C.RESET}")

        shutil.rmtree(Path(tmp_clone) / ".git", ignore_errors=True)
        new_dir.mkdir(parents=True, exist_ok=True)
        _merge_tree_no_clobber(tmp_clone, new_dir)

    cmake_path = new_dir / "CMakeLists.txt"
    if cmake_path.exists():
        print(t("init_port.adapting_cmake"))
        text = cmake_path.read_text()
        text = text.replace('project(so_loader C CXX)', f'project({ctx["project_name"]} C CXX)')
        text = text.replace('set(VITA_APP_NAME "so-loader")', f'set(VITA_APP_NAME "{ctx["game_name"]}")')
        text = text.replace('set(VITA_TITLEID "SOLOADER0")', f'set(VITA_TITLEID "{ctx["titleid"]}")')
        text = text.replace('set(VITA_VPKNAME "so_loader")', f'set(VITA_VPKNAME "{ctx["project_name"]}")')
        text = re.sub(r'set\(PSVITAIP "[^"]*"', f'set(PSVITAIP "{ctx["vita_ip"]}"', text)
        text = text.replace('ux0:data/gamename/', f'ux0:data/{ctx["slug"]}/')
        cmake_path.write_text(text)
        print(f"{C.GREEN}{t('init_port.cmake_adapted')}{C.RESET}")


def place_apk_and_detect(ctx):
    new_dir = ctx["new_dir"]
    apk_path = Path(ctx["apk_path"])
    apk_basename = apk_path.name
    apk_stem = apk_path.stem

    print(t("init_port.copying_apk"))
    dest_apk = new_dir / apk_basename
    if not _same_file(apk_path, dest_apk):
        shutil.copy2(apk_path, dest_apk)
    dest_zip = new_dir / f"{apk_stem}.zip"
    if not _same_file(apk_path, dest_zip):
        shutil.copy2(apk_path, dest_zip)

    extract_dir = new_dir / f"{ctx['slug']}_extract"
    print(t("init_port.extracting_apk", dirname=extract_dir.name))
    extract_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["unzip", "-qq", "-o", str(apk_path), "-d", str(extract_dir)])

    abis = []
    lib_dir = extract_dir / "lib"
    if lib_dir.is_dir():
        abis = sorted(d.name for d in lib_dir.iterdir() if d.is_dir())

    preferred_abi = "armeabi-v7a" if "armeabi-v7a" in abis else (abis[0] if abis else None)

    abis_str = ', '.join(abis) or t("init_port.none")
    print(f"\n{t('init_port.abis_found', abis=abis_str)}")
    arch_note = _arch_note(abis, preferred_abi)
    print(f"[+] {arch_note}")

    so_files = []
    if preferred_abi:
        so_files = sorted((lib_dir / preferred_abi).glob("*.so"))
    print(f"\n{t('init_port.so_files_found', abi=preferred_abi)}")
    for f in so_files:
        size_kb = f.stat().st_size / 1024
        print(f"    {f.name}  ({size_kb:.0f} KB)")

    gles_hint = t("init_port.gles_undetermined")
    if so_files and _have("objdump"):
        gles1 = gles2 = gles3 = 0
        for so in so_files:
            r = subprocess.run(["objdump", "-T", str(so)], capture_output=True, text=True)
            syms = set(re.findall(r"gl[A-Za-z0-9_]*", r.stdout))
            if syms & {"glVertexPointer", "glClearColorx", "glTexParameterx", "glColor4x"}:
                gles1 += 1
            if syms & {"glCreateShader", "glCreateProgram", "glUseProgram", "glGetUniformLocation"}:
                gles2 += 1
            if syms & {"glDrawArraysInstanced", "glDrawRangeElements", "glGenVertexArrays", "glBindVertexArray"}:
                gles3 += 1
        if gles3:
            gles_hint = t("init_port.gles3_detected")
        elif gles2:
            gles_hint = t("init_port.gles2_detected")
        elif gles1:
            gles_hint = t("init_port.gles1_detected")
        else:
            gles_hint = t("init_port.gles_no_signal")
    print(t("init_port.gl_heuristic", gles_hint=gles_hint))

    manifest = extract_dir / "AndroidManifest.xml"
    java_package = ""
    if manifest.exists():
        m = re.search(rb'package="([^"]*)"', manifest.read_bytes())
        if m:
            java_package = m.group(1).decode(errors="ignore")

    ctx.update({
        "apk_basename": apk_basename, "extract_dir": extract_dir,
        "abis": abis, "preferred_abi": preferred_abi, "arch_note": arch_note,
        "so_files": so_files, "gles_hint": gles_hint, "java_package": java_package,
    })


def decompile(global_cfg, ctx, have_jadx, have_docker_so, skip_ghidra=False, skip_jadx=False):
    new_dir = ctx["new_dir"]
    decompiled_dir = new_dir / "decompiled"
    apk_out_dir = decompiled_dir / "apk_jadx"
    apk_out_dir.mkdir(parents=True, exist_ok=True)

    jadx_ok = False
    gles_final = ctx["gles_hint"]
    if skip_jadx:
        print(f"{C.YELLOW}[*] Se omite decompilación Java con jadx (--skip-jadx).{C.RESET}")
    elif (apk_out_dir / "resources" / "AndroidManifest.xml").exists() or (apk_out_dir / "sources").exists():
        print(f"{C.GREEN}{t('init_port.jadx_already_done')}{C.RESET}")
        jadx_ok = True
    elif have_jadx:
        print(t("init_port.decompiling_java"))
        try:
            r = subprocess.run(["jadx", "-d", str(apk_out_dir), str(new_dir / ctx["apk_basename"])])
            jadx_ok = r.returncode == 0
            print(t("init_port.jadx_ok") if jadx_ok else t("init_port.jadx_errors"))
        except KeyboardInterrupt:
            print(f"\n{C.YELLOW}{t('init_port.jadx_interrupted')}{C.RESET}")
            jadx_ok = False
    else:
        gles_final = t("init_port.gles_no_jadx", gles_hint=ctx['gles_hint'])

    manifest_decoded = apk_out_dir / "resources" / "AndroidManifest.xml"
    if manifest_decoded.exists():
        text = manifest_decoded.read_text(errors="ignore")
        m = re.search(r'glEsVersion="([^"]*)"', text)
        if m:
            gles_map = {"0x00010000": "GLES1", "65536": "GLES1",
                        "0x00020000": "GLES2", "131072": "GLES2",
                        "0x00030000": "GLES3", "196608": "GLES3"}
            gles_final = gles_map.get(m.group(1), t("init_port.gles_nonstandard", value=m.group(1))) + t("init_port.gles_declared_suffix")
        else:
            gles_final = t("init_port.gles_no_manifest_declare", gles_hint=ctx['gles_hint'])
        m = re.search(r'package="([^"]*)"', text)
        if m:
            ctx["java_package"] = m.group(1)
    else:
        if not gles_final or gles_final == ctx["gles_hint"]:
            gles_final = t("init_port.gles_no_jadx" if not jadx_ok else "init_port.gles_manifest_unreadable", gles_hint=ctx['gles_hint'])

    print(f"\n{t('init_port.gles_final_version', gles=gles_final)}")

    ghidra_ok = False
    if skip_ghidra:
        print(f"\n{C.YELLOW}{t('init_port.so_decompile_skipped_flag')}{C.RESET}")
    elif have_docker_so and ctx.get("so_files"):
        for so_file in ctx["so_files"]:
            abi = so_file.parent.name
            so_out = decompiled_dir / f"{so_file.stem}_{abi}" / "ghidra"
            so_out.mkdir(parents=True, exist_ok=True)
            if (so_out / "out_ghidra.c").exists() and (so_out / "out_ghidra.c").stat().st_size > 0:
                print(f"{C.GREEN}[+] {so_file.name} ({abi}) ya tiene decompilación en {so_out / 'out_ghidra.c'}. Reutilizando.{C.RESET}")
                ghidra_ok = True
                continue
            print(t("init_port.decompiling_so", name=so_file.name, abi=abi))
            try:
                r = subprocess.run([
                    "docker", "run", "--rm", "--platform", "linux/amd64",
                    "-v", f"{so_file.parent}:/input", "-v", f"{so_out}:/output",
                    "devrvk/so-decompiler", "decompile", f"/input/{so_file.name}", "/output",
                ])
                if r.returncode == 0:
                    print(t("init_port.so_decompile_ok", path=so_out))
                    ghidra_ok = True
                else:
                    print(t("init_port.so_decompile_failed", name=so_file.name))
            except KeyboardInterrupt:
                print(f"\n{C.YELLOW}{t('init_port.so_decompile_interrupted')}{C.RESET}")
                print(f"{C.YELLOW}[*] Continuando con los siguientes procesos y creación de planes...{C.RESET}")
                ghidra_ok = False
                break
    else:
        print(f"{C.YELLOW}{t('init_port.so_decompile_skipped')}{C.RESET}")

    ctx["jadx_ok"] = jadx_ok
    ctx["have_docker_so"] = have_docker_so and ghidra_ok
    ctx["ghidra_ok"] = ghidra_ok
    ctx["ghidra_skipped"] = skip_ghidra or not ghidra_ok
    ctx["gles_final"] = gles_final


def git_init_and_ignore(ctx):
    new_dir = ctx["new_dir"]
    slug = ctx["slug"]
    print(t("init_port.git_init_start"))
    if not (new_dir / ".git").is_dir():
        _sh(["git", "init", "-q"], cwd=new_dir)

    gitignore = f"""# macOS metadata
.DS_Store
._*
.Spotlight-V100
.Trashes

# Android APK/ZIP originales y extracción -- nunca commitear el juego (DMCA)
*.apk
*.zip
/{slug}_extract/

# Java decompilado con jadx (derivado, regenerable: jadx -d decompiled/apk_jadx "{ctx['apk_basename']}")
/decompiled/apk_jadx/

# Pseudo-C decompilado del/los .so (derivado, regenerable con devrvk/so-decompiler)
/decompiled/*/ghidra/

# Librerías .so propietarias del juego original
lib/*.so
lib/**/*.so
{slug}_extract/lib/

# Assets del juego montados para pruebas
ux0_data/
assets/

# Build artifacts
/build/
CMakeCache.txt
CMakeFiles/
Makefile
cmake_install.cmake
*.elf
*.self
*.vpk
*.suprx

# Debugging en consola real
/logs/
log_*.txt
*_???.log
*.psp2dmp

# Python
__pycache__/
*.pyc

# Config local del toolkit (contiene IP de tu Vita -- no es secreto pero es de tu red)
.psvita-toolkit.json

# IDE
.vscode/
.idea/
*.swp
cmake-build-*/
"""
    (new_dir / ".gitignore").write_text(gitignore)
    print(f"{C.GREEN}{t('init_port.gitignore_written')}{C.RESET}")


def _arch_note(abis, preferred_abi):
    if not preferred_abi:
        note = t("init_port.arch_note_no_abi")
    elif preferred_abi == "armeabi-v7a":
        note = t("init_port.arch_note_v7a")
    else:
        note = t("init_port.arch_note_v6")
    if len(abis) > 1:
        note += t("init_port.arch_note_multi_abi", abis=', '.join(abis), preferred=preferred_abi)
    return note


def _plan_gles(gles_final, declared):
    """English GLES line for the plan; gles_final itself is in the UI language."""
    m = re.search(r"GLES\d", gles_final or "")
    if not m:
        return t("init_port.gles_undetermined") + " -- confirm with Ghidra / AndroidManifest.xml (glEsVersion)"
    if declared:
        return m.group(0) + t("init_port.gles_declared_suffix")
    return m.group(0) + " (symbol heuristic only -- confirm glEsVersion in AndroidManifest.xml)"


def write_plan_and_progress(ctx):
    import datetime
    new_dir = ctx["new_dir"]
    today = datetime.date.today().isoformat()
    # PORTING_PLAN.md is read by AI agents: always English, whatever the UI language.
    ui_lang = i18n.get_language()
    gles_declared = ctx.get("gles_final", "").endswith(t("init_port.gles_declared_suffix"))
    i18n.set_language("en")

    so_list = "".join(
        f"- `{f.relative_to(new_dir)}` ({f.stat().st_size / 1024:.0f} KB)\n"
        for f in ctx.get("so_files", [])
    ) or t("init_port.plan_so_none")

    jni_exports = ""
    if ctx.get("so_files") and _have("objdump"):
        all_names = set()
        for sf in ctx["so_files"]:
            try:
                r = subprocess.run(["objdump", "-T", str(sf)], capture_output=True, text=True)
                all_names.update(re.findall(r"Java_\S+", r.stdout))
            except Exception:
                pass
        if all_names:
            jni_exports = "".join(f"- `{n}`\n" for n in sorted(all_names))
    if not jni_exports:
        jni_exports = t("init_port.plan_jni_none")

    abis_str = ', '.join(ctx.get('abis', [])) or t("init_port.none")
    package_label = ctx.get('java_package') or t("init_port.plan_package_pending")

    plan = f"""# {t('init_port.plan_title', game_name=ctx['game_name'])}

> {t('init_port.plan_intro', today=today)}

> **For AI agents:** this file is the port's source of truth. Work phases in order. Tick `[x]` only
> after confirming on real hardware and append the evidence (log file, commit, dump) to the item.
> Never record an engine fact without symbol/decompilation evidence. Keep this file in English.

## {t('init_port.plan_section0_title')}

- **{t('init_port.plan_game_label')}** {ctx['game_name']}
- **{t('init_port.plan_package_label')}** {package_label}
- **{t('init_port.plan_apk_label')}** `{ctx.get('apk_basename', '')}`
- **{t('init_port.plan_titleid_label')}** `{ctx['titleid']}`

{t('init_port.plan_engine_known')}

## {t('init_port.plan_section1_title')}

- **{t('init_port.plan_abi_label')}** {abis_str}
- **{t('init_port.plan_abi_chosen_label')}** {ctx.get('preferred_abi') or 'N/A'}
- **{t('init_port.plan_arch_note_label')}** {_arch_note(ctx.get('abis', []), ctx.get('preferred_abi'))}
- **{t('init_port.plan_gles_label')}** {_plan_gles(ctx.get('gles_final', ''), gles_declared)}

## {t('init_port.plan_section2_title', abi=ctx.get('preferred_abi') or 'N/A')}

{so_list}

## {t('init_port.plan_section3_title')}

{jni_exports}

## {t('init_port.plan_section4_title')}

{t('init_port.plan_phases_intro')}

### {t('init_port.plan_phase1_title')}
- [x] {t('init_port.plan_check_repo')}

### {t('init_port.plan_phase2_title')}
- [{'x' if ctx.get('jadx_ok') or ctx.get('have_docker_so') or ctx.get('ghidra_ok') else ' '}] {t('init_port.plan_check_decompiled')}

""" + "\n\n".join(t(f"init_port.plan_phase{n}", titleid=ctx['titleid']) for n in range(3, 16)) + f"""

## {t('init_port.plan_section5_title')}

{t('init_port.plan_tools_text')}

## {t('init_port.plan_section6_title')}

{t('init_port.plan_log_text', slug=ctx['slug'])}
"""
    i18n.set_language(ui_lang)
    (new_dir / "PORTING_PLAN.md").write_text(plan)
    print(f"{C.GREEN}{t('init_port.plan_written')}{C.RESET}")

    progress = f"""# {t('init_port.progress_title', game_name=ctx['game_name'])}

## {t('init_port.progress_phase1_title', today=today)}
- {t('init_port.progress_p1_repo')}
- {t('init_port.progress_p1_apk', apk=ctx.get('apk_basename', ''))}
- {t('init_port.progress_p1_abi', abis=abis_str, preferred=ctx.get('preferred_abi') or 'N/A')}
- {t('init_port.progress_p1_gles', gles=ctx.get('gles_final', ''))}

## {t('init_port.progress_phase2_title', today=today)}
- {t('init_port.progress_p2_jadx_done') if ctx.get('jadx_ok') else t('init_port.progress_p2_jadx_pending')}
- {t('init_port.progress_p2_ghidra_done') if (ctx.get('have_docker_so') or ctx.get('ghidra_ok')) else t('init_port.progress_p2_ghidra_pending')}

## {t('init_port.progress_phase3_title')}
- [ ] {t('init_port.progress_p3_confirm_engine')}
- [ ] {t('init_port.progress_p3_read_sources')}
- [ ] {t('init_port.progress_p3_confirm_jni')}

## {t('init_port.progress_phase4_title')}
{t('init_port.progress_see_plan')}
"""
    (new_dir / "port_progress.md").write_text(progress)
    print(f"{C.GREEN}{t('init_port.progress_written')}{C.RESET}")


def write_claude_md_and_skills(global_cfg, ctx):
    new_dir = ctx["new_dir"]
    skills_sources = []
    if global_cfg.get("skills_source"):
        skills_sources.append(Path(global_cfg["skills_source"]))
    skills_sources.extend([
        Path.home() / ".gemini" / "config" / "skills",
        Path.home() / ".claude" / "skills",
        Path("/Volumes/TOSHIBA EXT/PSVITA Develop/psvita-port-toolkit/.claude/skills"),
    ])

    skills_dest = new_dir / ".claude" / "skills"
    skills_dest.mkdir(parents=True, exist_ok=True)

    for skill in ("psvita-porting", "so-crash-triage", "psvita-port-init"):
        src = None
        for candidate_base in skills_sources:
            cand = candidate_base / skill
            if cand.is_dir() and (cand / "SKILL.md").exists():
                src = cand
                break
        if src and src.is_dir():
            dst = skills_dest / skill
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("._*"))
            print(f"{C.GREEN}{t('init_port.skill_copied', skill=skill)}{C.RESET}")
        else:
            print(f"{C.YELLOW}{t('init_port.skill_not_found', skill=skill, source=global_cfg.get('skills_source', ''))}{C.RESET}")

    claude_md = f"""# {t('init_port.claude_md_title', game_name=ctx['game_name'])}

{t('init_port.claude_md_intro', apk=ctx.get('apk_basename', ''))}

## {t('init_port.claude_md_structure_title')}

- `{ctx['slug']}_extract/` — {t('init_port.claude_md_struct_extract')}
- `decompiled/` — {t('init_port.claude_md_struct_decompiled')}
- `source/`, `lib/so_util`, `lib/falso_jni` — {t('init_port.claude_md_struct_scaffold')}
- `PORTING_PLAN.md` — {t('init_port.claude_md_struct_plan')}
- `port_progress.md` — {t('init_port.claude_md_struct_progress')}
- `.psvita-toolkit.json` — {t('init_port.claude_md_struct_config')}

{t('init_port.claude_md_no_porting_tools')}

## {t('init_port.claude_md_findings_title')}

- {t('init_port.claude_md_finding_abi', abis=', '.join(ctx.get('abis', [])) or t('init_port.none'), preferred=ctx.get('preferred_abi') or 'N/A')}
- {t('init_port.claude_md_finding_gles', gles=ctx.get('gles_final', ''))}
- {t('init_port.claude_md_finding_package', package=ctx.get('java_package') or t('init_port.pending'))}

## {t('init_port.claude_md_workflow_title')}

1. {t('init_port.claude_md_wf1')}
2. {t('init_port.claude_md_wf2')}
3. {t('init_port.claude_md_wf3')}
4. {t('init_port.claude_md_wf4')}
5. {t('init_port.claude_md_wf5')}
"""
    (new_dir / "CLAUDE.md").write_text(claude_md)
    print(f"{C.GREEN}{t('init_port.claude_md_written')}{C.RESET}")


def analyze_engine_and_jni(ctx):
    """!
    @brief Post-decompile step: fingerprint known middleware in the primary
           `.so`, generate FalsoJNI callback-stub candidates from the
           decompiled Java, and document detected lifecycle methods in the
           freshly-written `PORTING_PLAN.md`.
    @param ctx Wizard context dict (needs `new_dir`).
    @note Best-effort and non-fatal: an engine with no `native` methods
          found yet (e.g. jadx wasn't available) simply skips this silently
          -- it's meant to save time when the sources are already there, not
          to block port creation when they aren't.
    """
    from . import jni_analyzer
    new_dir = ctx["new_dir"]
    print(f"\n{C.BOLD}{t('init_port.analyzing_engine_title')}{C.RESET}")
    jni_analyzer.middleware_report({"_project_dir": str(new_dir)})
    jni_analyzer.generate_jni_stubs({"_project_dir": str(new_dir)})
    jni_analyzer.document_lifecycle_in_plan({"_project_dir": str(new_dir)})


def write_project_config(ctx):
    project_cfg = cfgmod.new_project_config(
        game_name=ctx["game_name"], slug=ctx["slug"],
        project_name=ctx["project_name"], titleid=ctx["titleid"],
        vita_ip=ctx["vita_ip"], apk_basename=ctx["apk_basename"],
    )
    cfgmod.save_project_config(ctx["new_dir"], project_cfg)
    print(f"{C.GREEN}{t('init_port.project_config_written', new_dir=ctx['new_dir'])}{C.RESET}")
    project_cfg["_project_dir"] = str(ctx["new_dir"])
    return project_cfg


def check_port_completeness(project_dir):
    """!
    @brief Audits whether a port directory has all required assets, scaffolding,
           plans, configs, git repos, and decompilations.
    @param project_dir Path to the port directory.
    @return dict with completeness analysis.
    """
    project_dir = Path(project_dir).resolve()
    details = {}
    missing = []
    present = []
    metadata = {}

    if not project_dir.is_dir():
        return {
            "is_complete": False,
            "is_incomplete": True,
            "missing": [f"Directorio no existe: {project_dir}"],
            "present": [],
            "details": {},
            "metadata": {},
        }

    # 1. Config (.psvita-toolkit.json)
    cfg_path = project_dir / ".psvita-toolkit.json"
    has_cfg = cfg_path.is_file()
    details["config (.psvita-toolkit.json)"] = has_cfg
    if has_cfg:
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
            present.append("Configuración (.psvita-toolkit.json)")
        except Exception:
            details["config (.psvita-toolkit.json)"] = False
            missing.append("Configuración corrupta (.psvita-toolkit.json)")
    else:
        missing.append("Configuración (.psvita-toolkit.json)")

    # 2. CMakeLists.txt
    cmake_path = project_dir / "CMakeLists.txt"
    has_cmake = cmake_path.is_file()
    if has_cmake:
        text = cmake_path.read_text(errors="ignore")
        m_title = re.search(r'VITA_TITLEID\s+"([A-Za-z0-9]{9})"', text)
        if m_title and m_title.group(1) != "SOLOADER0":
            details["CMakeLists.txt (adaptado)"] = True
            present.append(f"CMakeLists.txt (TITLEID: {m_title.group(1)})")
            metadata.setdefault("titleid", m_title.group(1))
        else:
            details["CMakeLists.txt (adaptado)"] = False
            missing.append("CMakeLists.txt no adaptado (TITLEID pendiente)")
        m_app = re.search(r'VITA_APP_NAME\s+"([^"]+)"', text)
        if m_app:
            metadata.setdefault("game_name", m_app.group(1))
        m_proj = re.search(r'project\(([A-Za-z0-9_]+)', text)
        if m_proj:
            metadata.setdefault("project_name", m_proj.group(1))
        m_data = re.search(r'ux0:data/([^/]+)/', text)
        if m_data:
            metadata.setdefault("slug", m_data.group(1))
        m_ip = re.search(r'set\(PSVITAIP\s+"([^"]+)"', text)
        if m_ip:
            metadata.setdefault("vita_ip", m_ip.group(1))
    else:
        details["CMakeLists.txt (adaptado)"] = False
        missing.append("CMakeLists.txt")

    # 3. Git repo & .gitignore
    has_git = (project_dir / ".git").is_dir()
    details["Git (.git/)"] = has_git
    if has_git:
        present.append("Repositorio Git (.git/)")
    else:
        missing.append("Repositorio Git (.git/)")

    gitignore_path = project_dir / ".gitignore"
    has_gitignore = gitignore_path.is_file() and gitignore_path.stat().st_size > 150
    details[".gitignore anti-DMCA"] = has_gitignore
    if has_gitignore:
        present.append(".gitignore con reglas anti-DMCA")
    else:
        missing.append(".gitignore completo (anti-DMCA)")

    # 4. Plans and documentation
    plan_path = project_dir / "PORTING_PLAN.md"
    has_plan = plan_path.is_file() and plan_path.stat().st_size > 100
    details["PORTING_PLAN.md"] = has_plan
    if has_plan:
        present.append("Plan de port (PORTING_PLAN.md)")
    else:
        missing.append("Plan de port (PORTING_PLAN.md)")

    progress_path = project_dir / "port_progress.md"
    has_progress = progress_path.is_file() and progress_path.stat().st_size > 50
    details["port_progress.md"] = has_progress
    if has_progress:
        present.append("Progreso (port_progress.md)")
    else:
        missing.append("Progreso (port_progress.md)")

    claude_path = project_dir / "CLAUDE.md"
    has_claude = claude_path.is_file() and claude_path.stat().st_size > 50
    details["CLAUDE.md"] = has_claude
    if has_claude:
        present.append("Instrucciones IA (CLAUDE.md)")
    else:
        missing.append("Instrucciones IA (CLAUDE.md)")

    skills_dir = project_dir / ".claude" / "skills"
    has_skills = skills_dir.is_dir() and any((skills_dir / s).is_dir() for s in ("psvita-porting", "so-crash-triage", "psvita-port-init"))
    details[".claude/skills/"] = has_skills
    if has_skills:
        present.append("Habilidades Claude/Antigravity (.claude/skills/)")
    else:
        missing.append("Habilidades (.claude/skills/)")

    # 5. Scaffold (source/ and lib/)
    has_scaffold = (project_dir / "source").is_dir() and (project_dir / "lib").is_dir()
    details["Scaffold (source/, lib/)"] = has_scaffold
    if has_scaffold:
        present.append("Scaffold soloader (source/, lib/)")
    else:
        missing.append("Scaffold soloader (source/, lib/)")

    # 6. APK / Extract
    has_apk = any(f for f in project_dir.glob("*.apk") if not f.name.startswith("._"))
    extract_dirs = [d for d in project_dir.glob("*_extract") if d.is_dir() and not d.name.startswith("._")]
    has_extract = bool(extract_dirs and any((d / "lib").is_dir() for d in extract_dirs))
    details["APK / extract"] = has_apk or has_extract
    if has_apk or has_extract:
        present.append("APK original y/o extracción con lib/")
    else:
        missing.append("APK o carpeta de extracción *_extract/")

    # 7. Decompiled Java (apk_jadx)
    jadx_dir = project_dir / "decompiled" / "apk_jadx"
    has_jadx = jadx_dir.is_dir() and ((jadx_dir / "sources").is_dir() or (jadx_dir / "resources").is_dir())
    details["Java decompilado (decompiled/apk_jadx)"] = has_jadx
    if has_jadx:
        present.append("Java decompilado (decompiled/apk_jadx)")
    else:
        missing.append("Java decompilado (decompiled/apk_jadx)")

    # 8. Decompiled Native (ghidra)
    ghidra_dirs = [d for d in project_dir.glob("decompiled/*/ghidra") if d.is_dir()]
    has_ghidra = any((d / "out_ghidra.c").is_file() or (d / "out_angr.c").is_file() for d in ghidra_dirs)
    details["Decompilación nativa (decompiled/*/ghidra)"] = has_ghidra
    if has_ghidra:
        present.append("Decompilación nativa (Ghidra)")
    else:
        missing.append("Decompilación nativa (Ghidra out_ghidra.c)")

    # 9. JNI analysis documented in plan
    has_jni_analysis = False
    if has_plan:
        plan_text = plan_path.read_text(errors="ignore")
        if "Auto-detected lifecycle methods" in plan_text or "Exports JNI detectados" in plan_text:
            has_jni_analysis = True
    details["Análisis JNI en plan"] = has_jni_analysis
    if has_jni_analysis:
        present.append("Métodos JNI y ciclo de vida en PORTING_PLAN.md")
    else:
        missing.append("Métodos JNI / ciclo de vida documentados en plan")

    is_complete = (
        has_cfg and has_cmake and has_git and has_gitignore and
        has_plan and has_progress and has_claude and has_skills and
        has_scaffold and (has_apk or has_extract)
    )

    return {
        "is_complete": is_complete,
        "is_incomplete": not is_complete,
        "missing": missing,
        "present": present,
        "details": details,
        "metadata": metadata,
    }


def complete_port(project_dir, global_cfg=None, skip_ghidra=False, skip_jadx=False):
    """!
    @brief Completes an incomplete or interrupted port, adding any missing
           scaffolding, git initialization, anti-DMCA .gitignore, plans
           (PORTING_PLAN.md, port_progress.md, CLAUDE.md, .claude/skills),
           JNI stubs, and .psvita-toolkit.json.
    @param project_dir Path to the port directory.
    @param global_cfg Global config dict (loaded if None).
    @param skip_ghidra If True, do not attempt to run Ghidra decompiler.
    @param skip_jadx If True, do not attempt to run jadx decompiler.
    @return Ready-to-use per-project config dict.
    """
    project_dir = Path(project_dir).resolve()
    if not project_dir.is_dir():
        raise RuntimeError(f"El directorio no existe: {project_dir}")

    if global_cfg is None:
        global_cfg = cfgmod.load_global_config()

    print(f"\n{C.BOLD}{t('init_port.completing_port', path=project_dir)}{C.RESET}")
    audit = check_port_completeness(project_dir)
    if audit["missing"]:
        print(f"[*] Elementos faltantes detectados ({len(audit['missing'])}):")
        for m in audit["missing"]:
            print(f"    - {m}")
    else:
        print(f"{C.GREEN}[+] No faltan componentes estructurales.{C.RESET}")

    meta = audit.get("metadata", {})
    cmake_path = project_dir / "CMakeLists.txt"
    cmake_text = cmake_path.read_text(errors="ignore") if cmake_path.exists() else ""

    game_name = meta.get("game_name")
    if not game_name:
        m_app = re.search(r'VITA_APP_NAME\s+"([^"]+)"', cmake_text)
        if m_app and m_app.group(1) != "so-loader":
            game_name = m_app.group(1)
        else:
            folder_clean = project_dir.name.replace("-vita", "").replace("-Vita", "").replace("_vita", "")
            game_name = folder_clean.replace("-", " ").replace("_", " ").title()

    slug = meta.get("slug")
    if not slug:
        m_data = re.search(r'ux0:data/([^/]+)/', cmake_text)
        if m_data and m_data.group(1) != "gamename":
            slug = m_data.group(1)
        else:
            slug = _default_slug(game_name)

    project_name = meta.get("project_name")
    if not project_name:
        m_proj = re.search(r'project\(([A-Za-z0-9_]+)', cmake_text)
        if m_proj and m_proj.group(1) != "so_loader":
            project_name = m_proj.group(1)
        else:
            project_name = slug.replace("-", "_")

    titleid = meta.get("titleid") or _own_titleid(project_dir)
    if not titleid:
        m_tid = re.search(r'VITA_TITLEID\s+"([A-Za-z0-9]{9})"', cmake_text)
        if m_tid and m_tid.group(1) != "SOLOADER0":
            titleid = m_tid.group(1)
        else:
            base_dir = Path(global_cfg.get("base_dir", project_dir.parent))
            used = _used_titleids(base_dir)
            titleid = f"PSV{abs(hash(slug)) % 1000000:06d}"
            while titleid in used:
                titleid = f"PSV{abs(hash(titleid + 'x')) % 1000000:06d}"

    vita_ip = meta.get("vita_ip")
    if not vita_ip:
        m_ip = re.search(r'set\(PSVITAIP\s+"([^"]+)"', cmake_text)
        if m_ip:
            vita_ip = m_ip.group(1)
        else:
            vita_ip = "192.168.1.100"

    # Find APK
    apks = [f for f in project_dir.glob("*.apk") if not f.name.startswith("._")]
    if not apks:
        apks = [f for f in project_dir.parent.glob("*.apk") if not f.name.startswith("._") and slug in f.name.lower()]
    apk_path = str(apks[0]) if apks else ""
    apk_basename = apks[0].name if apks else "(extracted)"

    # Find extract dir
    extract_dirs = [d for d in project_dir.glob("*_extract") if d.is_dir() and not d.name.startswith("._")]
    if extract_dirs:
        extract_dir = extract_dirs[0]
    else:
        extract_dir = project_dir / f"{slug}_extract"

    if (not extract_dir.exists() or not any(extract_dir.iterdir())) and apk_path:
        print(t("init_port.extracting_apk", dirname=extract_dir.name))
        extract_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["unzip", "-qq", "-o", apk_path, "-d", str(extract_dir)])

    # Detect ABIs & SOs
    abis = []
    lib_dir = extract_dir / "lib"
    if not lib_dir.is_dir():
        lib_dir = project_dir / "lib"
    if lib_dir.is_dir():
        abis = sorted(d.name for d in lib_dir.iterdir() if d.is_dir() and not d.name.startswith("._"))

    preferred_abi = "armeabi-v7a" if "armeabi-v7a" in abis else (abis[0] if abis else None)

    arch_note = _arch_note(abis, preferred_abi)

    so_files = []
    if preferred_abi and (lib_dir / preferred_abi).is_dir():
        so_files = sorted(f for f in (lib_dir / preferred_abi).glob("*.so") if not f.name.startswith("._"))
    if not so_files and extract_dir.is_dir():
        so_files = sorted(f for f in extract_dir.glob("**/*.so") if not f.name.startswith("._"))

    # GLES detection
    gles_hint = t("init_port.gles_undetermined")
    if so_files and _have("objdump"):
        gles1 = gles2 = gles3 = 0
        for so in so_files:
            try:
                r = subprocess.run(["objdump", "-T", str(so)], capture_output=True, text=True)
                syms = set(re.findall(r"gl[A-Za-z0-9_]*", r.stdout))
                if syms & {"glVertexPointer", "glClearColorx", "glTexParameterx", "glColor4x"}:
                    gles1 += 1
                if syms & {"glCreateShader", "glCreateProgram", "glUseProgram", "glGetUniformLocation"}:
                    gles2 += 1
                if syms & {"glDrawArraysInstanced", "glDrawRangeElements", "glGenVertexArrays", "glBindVertexArray"}:
                    gles3 += 1
            except Exception:
                pass
        if gles3:
            gles_hint = t("init_port.gles3_detected")
        elif gles2:
            gles_hint = t("init_port.gles2_detected")
        elif gles1:
            gles_hint = t("init_port.gles1_detected")
        else:
            gles_hint = t("init_port.gles_no_signal")

    manifest = extract_dir / "AndroidManifest.xml"
    java_package = ""
    if manifest.exists():
        try:
            m = re.search(rb'package="([^"]*)"', manifest.read_bytes())
            if m:
                java_package = m.group(1).decode(errors="ignore")
        except Exception:
            pass

    ctx = {
        "game_name": game_name, "slug": slug, "folder_name": project_dir.name,
        "project_name": project_name, "apk_path": apk_path,
        "apk_basename": apk_basename, "vita_ip": vita_ip,
        "titleid": titleid, "new_dir": project_dir,
        "extract_dir": extract_dir, "abis": abis,
        "preferred_abi": preferred_abi, "arch_note": arch_note,
        "so_files": so_files, "gles_hint": gles_hint,
        "java_package": java_package, "gles_final": gles_hint,
    }

    have_jadx, have_docker_so = check_prereqs(global_cfg)

    # 1. Boilerplate / CMakeLists
    if not (project_dir / "CMakeLists.txt").exists():
        setup_repo_dir(global_cfg, ctx)
    else:
        cmake_path = project_dir / "CMakeLists.txt"
        text = cmake_path.read_text(errors="ignore")
        changed = False
        if "SOLOADER0" in text and titleid:
            text = text.replace('set(VITA_TITLEID "SOLOADER0")', f'set(VITA_TITLEID "{titleid}")')
            changed = True
        if 'project(so_loader C CXX)' in text and project_name:
            text = text.replace('project(so_loader C CXX)', f'project({project_name} C CXX)')
            changed = True
        if 'set(VITA_APP_NAME "so-loader")' in text and game_name:
            text = text.replace('set(VITA_APP_NAME "so-loader")', f'set(VITA_APP_NAME "{game_name}")')
            changed = True
        if changed:
            cmake_path.write_text(text)

    # 2. Decompile step
    decompile(global_cfg, ctx, have_jadx, have_docker_so, skip_ghidra=skip_ghidra, skip_jadx=skip_jadx)

    # 3. Git init & .gitignore
    if not (project_dir / ".git").is_dir():
        git_init_and_ignore(ctx)
    else:
        gi = project_dir / ".gitignore"
        if not gi.exists() or gi.stat().st_size < 150:
            git_init_and_ignore(ctx)

    # 4. PORTING_PLAN.md and port_progress.md
    if not (project_dir / "PORTING_PLAN.md").exists() or not (project_dir / "port_progress.md").exists():
        write_plan_and_progress(ctx)

    # 5. Engine and JNI analysis
    analyze_engine_and_jni(ctx)

    # 6. CLAUDE.md and .claude/skills
    if not (project_dir / "CLAUDE.md").exists() or not (project_dir / ".claude" / "skills").is_dir():
        write_claude_md_and_skills(global_cfg, ctx)

    # 7. Project config
    if not (project_dir / ".psvita-toolkit.json").exists():
        project_cfg = write_project_config(ctx)
    else:
        project_cfg = cfgmod.load_project_config(project_dir)
        project_cfg["_project_dir"] = str(project_dir)

    print(f"\n{C.GREEN}{t('init_port.port_complete_ok')}{C.RESET}")
    return project_cfg


def run_wizard(global_cfg):
    """!
    @brief Entry point: run the full new-port creation wizard.
    @param global_cfg Global config dict.
    @return Ready-to-use per-project config dict, or `None` if the user
            cancelled at any point.
    """
    try:
        have_jadx, have_docker_so = check_prereqs(global_cfg)
        ctx = prompt_inputs(global_cfg)
        new_dir = ctx["new_dir"]

        if new_dir.exists() and any(new_dir.iterdir()):
            audit = check_port_completeness(new_dir)
            if audit["is_incomplete"]:
                print(f"\n{C.YELLOW}[*] La carpeta {new_dir} ya existe y está incompleta ({len(audit['missing'])} faltantes).{C.RESET}")
                if tui.confirm("¿Deseas completarla/repararla agregando lo que falta?", default=True):
                    return complete_port(new_dir, global_cfg=global_cfg, skip_ghidra=ctx.get("skip_ghidra", False))

        tui.clear()
        tui.print_banner(t("init_port.creating_port_title", game_name=ctx['game_name']))
        setup_repo_dir(global_cfg, ctx)
        place_apk_and_detect(ctx)
        decompile(global_cfg, ctx, have_jadx, have_docker_so, skip_ghidra=ctx.get("skip_ghidra", False))
        git_init_and_ignore(ctx)
        write_plan_and_progress(ctx)
        analyze_engine_and_jni(ctx)
        write_claude_md_and_skills(global_cfg, ctx)
        project_cfg = write_project_config(ctx)

        print(f"\n{C.CYAN}{C.BOLD}================================================================{C.RESET}")
        print(f"{C.GREEN}{C.BOLD}  {t('init_port.wizard_done', new_dir=ctx['new_dir'])}{C.RESET}")
        print(f"{C.CYAN}{C.BOLD}================================================================{C.RESET}")
        print(t("init_port.next_step"))
        tui.pause()
        return project_cfg
    except RuntimeError as e:
        print(f"{C.RED}[-] {e}{C.RESET}")
        tui.pause()
        return None


def run_wizard_headless(global_cfg, apk_path=None, game_name=None, titleid=None, slug=None,
                         folder_name=None, vita_ip="192.168.1.100", project_path=None,
                         skip_ghidra=False, skip_jadx=False):
    """!
    @brief Non-interactive equivalent of `run_wizard()`, for the `psvita-toolkit
           init` CLI subcommand: builds the same `ctx` dict `prompt_inputs()`
           would have, from arguments instead of prompts, then runs the exact
           same pipeline (`setup_repo_dir` -> ... -> `write_project_config`).
           Supports resuming/completing an existing or incomplete port.
    @param global_cfg Global config dict.
    @param apk_path Path to the source `.apk`.
    @param game_name Display name of the game.
    @param titleid 9-character TITLEID.
    @param slug Internal slug.
    @param folder_name Destination folder name (under `base_dir`).
    @param vita_ip Test PS Vita's IP address.
    @param project_path Path to an existing project directory to complete/repair.
    @param skip_ghidra If True, skip Ghidra decompilation.
    @param skip_jadx If True, skip jadx decompilation.
    @return Ready-to-use per-project config dict.
    """
    if project_path:
        return complete_port(project_path, global_cfg=global_cfg, skip_ghidra=skip_ghidra, skip_jadx=skip_jadx)

    base_dir = Path(global_cfg.get("base_dir", "."))
    if folder_name:
        target_dir = base_dir / folder_name
        if target_dir.is_dir() and any(target_dir.iterdir()):
            return complete_port(target_dir, global_cfg=global_cfg, skip_ghidra=skip_ghidra, skip_jadx=skip_jadx)
    elif game_name:
        default_folder = game_name.replace(" ", "-") + "-vita"
        target_dir = base_dir / default_folder
        if target_dir.is_dir() and any(target_dir.iterdir()):
            return complete_port(target_dir, global_cfg=global_cfg, skip_ghidra=skip_ghidra, skip_jadx=skip_jadx)

    if not apk_path:
        raise RuntimeError("apk_path is required to create a new port from scratch.")
    apk_path = str(Path(apk_path).expanduser())
    if not Path(apk_path).exists():
        raise RuntimeError(t("tui.path_not_found", path=apk_path))
    if not game_name:
        raise RuntimeError(t("init_port.game_name_required"))

    slug = slug or _default_slug(game_name)
    folder_name = folder_name or (game_name.replace(" ", "-") + "-vita")
    project_name = slug.replace("-", "_")

    new_dir = base_dir / folder_name
    own_id = _own_titleid(new_dir)

    used_ids = _used_titleids(base_dir)
    if own_id:
        used_ids.discard(own_id)

    titleid = (titleid or own_id or "").strip().upper()
    if len(titleid) != 9:
        raise RuntimeError(t("init_port.titleid_length_error"))
    if titleid in used_ids:
        raise RuntimeError(t("init_port.titleid_in_use"))

    ctx = {
        "game_name": game_name, "slug": slug, "folder_name": folder_name,
        "project_name": project_name, "apk_path": apk_path,
        "vita_ip": vita_ip, "titleid": titleid, "new_dir": new_dir,
    }

    have_jadx, have_docker_so = check_prereqs(global_cfg)
    print(f"\n{C.BOLD}{t('init_port.creating_port_title', game_name=ctx['game_name'])}{C.RESET}")
    setup_repo_dir(global_cfg, ctx)
    place_apk_and_detect(ctx)
    decompile(global_cfg, ctx, have_jadx, have_docker_so, skip_ghidra=skip_ghidra, skip_jadx=skip_jadx)
    git_init_and_ignore(ctx)
    write_plan_and_progress(ctx)
    analyze_engine_and_jni(ctx)
    write_claude_md_and_skills(global_cfg, ctx)
    project_cfg = write_project_config(ctx)
    print(f"{C.GREEN}{t('init_port.wizard_done', new_dir=ctx['new_dir'])}{C.RESET}")
    return project_cfg
