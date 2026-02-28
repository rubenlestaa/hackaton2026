# rename_demo_chat.ps1 — rename project/subproject in demo.py and chat.py

foreach ($file in @(
    'c:\Users\Sergio\Desktop\HackUDC\ai-service\demo.py',
    'c:\Users\Sergio\Desktop\HackUDC\ai-service\chat.py'
)) {
    $c = Get-Content $file -Raw -Encoding UTF8

    # ── dict key accesses ──────────────────────────────────────────────────────
    $c = $c -replace 'result\.get\("is_new_subproject"\)', 'result.get("is_new_subgroup")'
    $c = $c -replace 'result\.get\("is_new_project"\)',    'result.get("is_new_group")'
    $c = $c -replace 'result\.get\("rename_project"\)',    'result.get("rename_group")'
    $c = $c -replace 'result\.get\("subproject"\)',        'result.get("subgroup")'
    $c = $c -replace 'result\.get\("project"\)',           'result.get("group")'
    $c = $c -replace 'result\.get\(''subproject''\)',      'result.get(''subgroup'')'
    $c = $c -replace 'result\.get\(''project'',',         'result.get(''group'','
    $c = $c -replace 'result\["rename_project"\]',         'result["rename_group"]'
    $c = $c -replace "result\[.subproject.\]",             'result["subgroup"]'
    $c = $c -replace "result\[.project.\]",                'result["group"]'
    $c = $c -replace 'result\.get\("is_new_subgroup"\)',   'result.get("is_new_subgroup")'  # already done, no-op

    # ── proj state dict ────────────────────────────────────────────────────────
    $c = $c -replace 'proj\["subprojects"\]',                'proj["subgroups"]'
    $c = $c -replace 'proj\.get\("subprojects",',            'proj.get("subgroups",'
    $c = $c -replace '"subprojects": \[\]',                  '"subgroups": []'
    $c = $c -replace '"subprojects": \[\}',                  '"subgroups": []}'
    $c = $c -replace '\.get\("subprojects", \[\]\)',         '.get("subgroups", [])'
    $c = $c -replace '\.get\(\"subprojects\", \[\]\)',       '.get("subgroups", [])'
    $c = $c -replace 'for sub in p\.get\(''subprojects''',  'for sub in p.get(''subgroups'''
    $c = $c -replace 'p\.get\("subprojects", \[\]\)',        'p.get("subgroups", [])'
    $c = $c -replace 'proj\["subprojects"\]\.append',        'proj["subgroups"].append'

    # ── state list variable ────────────────────────────────────────────────────
    $c = $c -replace '\bprojects\.append\b', 'groups.append'
    $c = $c -replace '\bprojects\.clear\b',  'groups.clear'
    $c = $c -replace '\bfor p in projects\b', 'for p in groups'
    $c = $c -replace '\bif not projects\b',   'if not groups'
    $c = $c -replace '"existing_projects": projects', '"existing_groups": groups'
    $c = $c -replace '"existing_projects":', '"existing_groups":'
    $c = $c -replace '\bprojects\b(?=\s*\))',  'groups'   # json=..., projects) 

    # Project list variable declaration
    $c = $c -replace '^projects: list\[dict\] = \[\]', 'groups: list[dict] = []'  # chat.py
    $c = $c -replace '^projects: list\[dict\] = \[\]\s*$', 'groups: list[dict] = []'

    # ── REST routes in demo.py ─────────────────────────────────────────────────
    $c = $c -replace '/projects/\{pname\}/subprojects/\{resolved_sp\}/ideas/\{idea\}',
                     '/groups/{pname}/subgroups/{resolved_sp}/ideas/{idea}'
    $c = $c -replace '/projects/\{pname\}/subprojects/\{spname\}/ideas',
                     '/groups/{pname}/subgroups/{spname}/ideas'
    $c = $c -replace '/projects/\{pname\}/subprojects',
                     '/groups/{pname}/subgroups'
    $c = $c -replace '/projects/\{pname\}/ideas',
                     '/groups/{pname}/ideas'
    $c = $c -replace '/projects/\{rename\[.old_name.\]\}',
                     '/groups/{rename[''old_name'']}'
    $c = $c -replace '"ruta":        "/projects"',
                     '"ruta":        "/groups"'
    $c = $c -replace '"/projects"',  '"/groups"'

    # ── Action labels ─────────────────────────────────────────────────────────
    $c = $c -replace '"RENOMBRAR PROYECTO"', '"RENOMBRAR GRUPO"'
    $c = $c -replace '"CREAR PROYECTO"',     '"CREAR GRUPO"'
    $c = $c -replace '"CREAR SUBPROYECTO"',  '"CREAR SUBGRUPO"'
    $c = $c -replace '"AÑADIR IDEA A SUBPROYECTO"', '"AÑADIR IDEA A SUBGRUPO"'
    $c = $c -replace '"AÑADIR IDEA AL PROYECTO"',   '"AÑADIR IDEA AL GRUPO"'

    # ── Display strings ───────────────────────────────────────────────────────
    $c = $c -replace 'nuevo proyecto',     'nuevo grupo'
    $c = $c -replace 'nuevo subproyecto',  'nuevo subgrupo'
    $c = $c -replace '"proyecto:    "',    '"grupo:    "'
    $c = $c -replace "'proyecto:    '",    "'grupo:    '"
    $c = $c -replace '       proyecto:    ', '       grupo:    '
    $c = $c -replace 'Proyectos actuales:', 'Grupos actuales:'
    $c = $c -replace '"Proyectos reseteados"', '"Grupos reseteados."'
    $c = $c -replace 'Proyectos reseteados\.', 'Grupos reseteados.'
    $c = $c -replace 'Estado actual de proyectos:', 'Estado actual de grupos:'
    $c = $c -replace 'sin proyectos', 'sin grupos'
    $c = $c -replace 'sin proyectos', 'sin grupos'

    # ── apply_result: rename variable ─────────────────────────────────────────
    $c = $c -replace '    rename = result\.get\("rename_group"\)', '    rename = result.get("rename_group")'

    [System.IO.File]::WriteAllText($file, $c, [System.Text.Encoding]::UTF8)
    Write-Host "$file done"
}
