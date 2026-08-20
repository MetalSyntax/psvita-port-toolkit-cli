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
    "init_port.plan_check_engine": {
        "es": "Análisis del motor real (ciclo de vida nativo, reuso de otro port o boilerplate genérico).",
        "en": "Analysis of the real engine (native lifecycle, reuse from another port or generic boilerplate).",
        "pt": "Análise do motor real (ciclo de vida nativo, reuso de outro port ou boilerplate genérico).",
    },
    "init_port.plan_check_bootstrap": {
        "es": "Bootstrap del loader: so_file_load/so_relocate/so_resolve, primer build.",
        "en": "Loader bootstrap: so_file_load/so_relocate/so_resolve, first build.",
        "pt": "Bootstrap do loader: so_file_load/so_relocate/so_resolve, primeiro build.",
    },
    "init_port.plan_check_jni_table": {
        "es": 'Tabla JNI (FalsoJNI): registrar exports + callbacks hacia "Java".',
        "en": 'JNI table (FalsoJNI): register exports + callbacks toward "Java".',
        "pt": 'Tabela JNI (FalsoJNI): registrar exports + callbacks para o "Java".',
    },
    "init_port.plan_check_first_boot": {
        "es": "Primer arranque en consola real.",
        "en": "First boot on real hardware.",
        "pt": "Primeira inicialização em hardware real.",
    },
    "init_port.plan_check_graphics": {
        "es": "Gráficos (wrappers GL según versión detectada).",
        "en": "Graphics (GL wrappers per detected version).",
        "pt": "Gráficos (wrappers GL conforme a versão detectada).",
    },
    "init_port.plan_check_input_audio": {
        "es": "Input, Audio, Assets, LiveArea/VPK.",
        "en": "Input, Audio, Assets, LiveArea/VPK.",
        "pt": "Input, Audio, Assets, LiveArea/VPK.",
    },
    "init_port.plan_check_hardware": {
        "es": "Pruebas en hardware real.",
        "en": "Tests on real hardware.",
        "pt": "Testes em hardware real.",
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

    return {
        "game_name": game_name, "slug": slug, "folder_name": folder_name,
        "project_name": project_name, "apk_path": apk_path,
        "vita_ip": vita_ip, "titleid": titleid, "new_dir": new_dir,
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
    if not preferred_abi:
        arch_note = t("init_port.arch_note_no_abi")
    elif preferred_abi == "armeabi-v7a":
        arch_note = t("init_port.arch_note_v7a")
    else:
        arch_note = t("init_port.arch_note_v6")
    if len(abis) > 1:
        arch_note += t("init_port.arch_note_multi_abi", abis=', '.join(abis), preferred=preferred_abi)
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


def decompile(global_cfg, ctx, have_jadx, have_docker_so):
    new_dir = ctx["new_dir"]
    decompiled_dir = new_dir / "decompiled"
    apk_out_dir = decompiled_dir / "apk_jadx"
    apk_out_dir.mkdir(parents=True, exist_ok=True)

    jadx_ok = False
    gles_final = ctx["gles_hint"]
    if have_jadx:
        print(t("init_port.decompiling_java"))
        r = subprocess.run(["jadx", "-d", str(apk_out_dir), str(new_dir / ctx["apk_basename"])])
        jadx_ok = r.returncode == 0
        print(t("init_port.jadx_ok") if jadx_ok else t("init_port.jadx_errors"))

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
            gles_final = t("init_port.gles_manifest_unreadable", gles_hint=ctx['gles_hint'])
    else:
        gles_final = t("init_port.gles_no_jadx", gles_hint=ctx['gles_hint'])

    print(f"\n{t('init_port.gles_final_version', gles=gles_final)}")

    if have_docker_so and ctx["so_files"]:
        for so_file in ctx["so_files"]:
            abi = so_file.parent.name
            so_out = decompiled_dir / f"{so_file.stem}_{abi}" / "ghidra"
            so_out.mkdir(parents=True, exist_ok=True)
            print(t("init_port.decompiling_so", name=so_file.name, abi=abi))
            r = subprocess.run([
                "docker", "run", "--rm", "--platform", "linux/amd64",
                "-v", f"{so_file.parent}:/input", "-v", f"{so_out}:/output",
                "devrvk/so-decompiler", "decompile", f"/input/{so_file.name}", "/output",
            ])
            print(t("init_port.so_decompile_ok", path=so_out) if r.returncode == 0 else t("init_port.so_decompile_failed", name=so_file.name))
    else:
        print(f"{C.YELLOW}{t('init_port.so_decompile_skipped')}{C.RESET}")

    ctx["jadx_ok"] = jadx_ok
    ctx["have_docker_so"] = have_docker_so
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


def write_plan_and_progress(ctx):
    import datetime
    new_dir = ctx["new_dir"]
    today = datetime.date.today().isoformat()

    so_list = "".join(
        f"- `{f.relative_to(new_dir)}` ({f.stat().st_size / 1024:.0f} KB)\n"
        for f in ctx["so_files"]
    ) or t("init_port.plan_so_none")

    jni_exports = ""
    if ctx["so_files"] and _have("objdump"):
        r = subprocess.run(["objdump", "-T", str(ctx["so_files"][0])], capture_output=True, text=True)
        names = sorted(set(re.findall(r"Java_\S+", r.stdout)))
        jni_exports = "".join(f"- `{n}`\n" for n in names)
    if not jni_exports:
        jni_exports = t("init_port.plan_jni_none")

    abis_str = ', '.join(ctx['abis']) or t("init_port.none")
    package_label = ctx['java_package'] or t("init_port.plan_package_pending")

    plan = f"""# {t('init_port.plan_title', game_name=ctx['game_name'])}

> {t('init_port.plan_intro', today=today)}

## {t('init_port.plan_section0_title')}

- **{t('init_port.plan_game_label')}** {ctx['game_name']}
- **{t('init_port.plan_package_label')}** {package_label}
- **{t('init_port.plan_apk_label')}** `{ctx['apk_basename']}`
- **{t('init_port.plan_titleid_label')}** `{ctx['titleid']}`

{t('init_port.plan_engine_known')}

## {t('init_port.plan_section1_title')}

- **{t('init_port.plan_abi_label')}** {abis_str}
- **{t('init_port.plan_abi_chosen_label')}** {ctx['preferred_abi'] or 'N/A'}
- **{t('init_port.plan_arch_note_label')}** {ctx['arch_note']}
- **{t('init_port.plan_gles_label')}** {ctx['gles_final']}

## {t('init_port.plan_section2_title', abi=ctx['preferred_abi'] or 'N/A')}

{so_list}

## {t('init_port.plan_section3_title')}

{jni_exports}

## {t('init_port.plan_section4_title')}

- [x] {t('init_port.plan_check_repo')}
- [x] {t('init_port.plan_check_decompiled')}
- [ ] {t('init_port.plan_check_engine')}
- [ ] {t('init_port.plan_check_bootstrap')}
- [ ] {t('init_port.plan_check_jni_table')}
- [ ] {t('init_port.plan_check_first_boot')}
- [ ] {t('init_port.plan_check_graphics')}
- [ ] {t('init_port.plan_check_input_audio')}
- [ ] {t('init_port.plan_check_hardware')}

## {t('init_port.plan_section5_title')}

{t('init_port.plan_tools_text')}
"""
    (new_dir / "PORTING_PLAN.md").write_text(plan)
    print(f"{C.GREEN}{t('init_port.plan_written')}{C.RESET}")

    progress = f"""# {t('init_port.progress_title', game_name=ctx['game_name'])}

## {t('init_port.progress_phase1_title', today=today)}
- {t('init_port.progress_p1_repo')}
- {t('init_port.progress_p1_apk', apk=ctx['apk_basename'])}
- {t('init_port.progress_p1_abi', abis=abis_str, preferred=ctx['preferred_abi'] or 'N/A')}
- {t('init_port.progress_p1_gles', gles=ctx['gles_final'])}

## {t('init_port.progress_phase2_title', today=today)}
- {t('init_port.progress_p2_jadx_done') if ctx['jadx_ok'] else t('init_port.progress_p2_jadx_pending')}
- {t('init_port.progress_p2_ghidra_done') if ctx['have_docker_so'] else t('init_port.progress_p2_ghidra_pending')}

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
    skills_source = Path(global_cfg["skills_source"])
    skills_dest = new_dir / ".claude" / "skills"
    skills_dest.mkdir(parents=True, exist_ok=True)

    for skill in ("psvita-porting", "so-crash-triage", "psvita-port-init"):
        src = skills_source / skill
        if src.is_dir():
            dst = skills_dest / skill
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("._*"))
            print(f"{C.GREEN}{t('init_port.skill_copied', skill=skill)}{C.RESET}")
        else:
            print(f"{C.YELLOW}{t('init_port.skill_not_found', skill=skill, source=skills_source)}{C.RESET}")

    claude_md = f"""# {t('init_port.claude_md_title', game_name=ctx['game_name'])}

{t('init_port.claude_md_intro', apk=ctx['apk_basename'])}

## {t('init_port.claude_md_structure_title')}

- `{ctx['slug']}_extract/` — {t('init_port.claude_md_struct_extract')}
- `decompiled/` — {t('init_port.claude_md_struct_decompiled')}
- `source/`, `lib/so_util`, `lib/falso_jni` — {t('init_port.claude_md_struct_scaffold')}
- `PORTING_PLAN.md` — {t('init_port.claude_md_struct_plan')}
- `port_progress.md` — {t('init_port.claude_md_struct_progress')}
- `.psvita-toolkit.json` — {t('init_port.claude_md_struct_config')}

{t('init_port.claude_md_no_porting_tools')}

## {t('init_port.claude_md_findings_title')}

- {t('init_port.claude_md_finding_abi', abis=', '.join(ctx['abis']) or t('init_port.none'), preferred=ctx['preferred_abi'] or 'N/A')}
- {t('init_port.claude_md_finding_gles', gles=ctx['gles_final'])}
- {t('init_port.claude_md_finding_package', package=ctx['java_package'] or t('init_port.pending'))}

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

        tui.clear()
        tui.print_banner(t("init_port.creating_port_title", game_name=ctx['game_name']))
        setup_repo_dir(global_cfg, ctx)
        place_apk_and_detect(ctx)
        decompile(global_cfg, ctx, have_jadx, have_docker_so)
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


def run_wizard_headless(global_cfg, apk_path, game_name, titleid=None, slug=None,
                         folder_name=None, vita_ip="192.168.1.100"):
    """!
    @brief Non-interactive equivalent of `run_wizard()`, for the `psvita-toolkit
           init` CLI subcommand: builds the same `ctx` dict `prompt_inputs()`
           would have, from arguments instead of prompts, then runs the exact
           same pipeline (`setup_repo_dir` -> ... -> `write_project_config`).
    @param global_cfg Global config dict.
    @param apk_path Path to the source `.apk` (required, must exist).
    @param game_name Display name of the game (required).
    @param titleid 9-character TITLEID; auto-assigned only if this project
           directory already has its own (see `_own_titleid()`) -- otherwise
           required, since there's no interactive collision-resolution retry.
    @param slug Internal slug; derived from `game_name` if omitted.
    @param folder_name Destination folder name (under `base_dir`); derived
           from `game_name` if omitted.
    @param vita_ip Test PS Vita's IP address.
    @return Ready-to-use per-project config dict.
    @raise RuntimeError on any invalid/missing/colliding input -- the CLI
           layer is expected to catch this and exit non-zero with the message.
    """
    apk_path = str(Path(apk_path).expanduser())
    if not Path(apk_path).exists():
        raise RuntimeError(t("tui.path_not_found", path=apk_path))
    if not game_name:
        raise RuntimeError(t("init_port.game_name_required"))

    slug = slug or _default_slug(game_name)
    folder_name = folder_name or (game_name.replace(" ", "-") + "-vita")
    project_name = slug.replace("-", "_")

    base_dir = Path(global_cfg["base_dir"])
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
    decompile(global_cfg, ctx, have_jadx, have_docker_so)
    git_init_and_ignore(ctx)
    write_plan_and_progress(ctx)
    analyze_engine_and_jni(ctx)
    write_claude_md_and_skills(global_cfg, ctx)
    project_cfg = write_project_config(ctx)
    print(f"{C.GREEN}{t('init_port.wizard_done', new_dir=ctx['new_dir'])}{C.RESET}")
    return project_cfg
