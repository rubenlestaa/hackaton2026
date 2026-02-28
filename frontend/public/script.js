// Backend URL — change to match your environment
const BACKEND_URL = 'http://localhost:8000';
const MAX_BUBBLES = 12;

// ── Navigation state ──────────────────────────────────────────────────────────
// Each frame: { title, items: [{type:'subgroup'|'idea', ...}], parentLabel }
let navStack = [];

document.addEventListener('DOMContentLoaded', () => {
    const searchInput   = document.getElementById('main-search-input');
    const projectsGrid  = document.getElementById('projects-grid');
    const projectsView  = document.getElementById('projects-view');
    const detailView    = document.getElementById('detail-view');
    const detailTitle   = document.getElementById('detail-title');
    const detailContent = document.getElementById('detail-content');
    const backButton    = document.getElementById('back-button');
    const backLabel     = document.getElementById('back-label');

    // ── Load groups from backend ──────────────────────────────────────────────
    function loadGroups() {
        fetch(`${BACKEND_URL}/inbox?status=processed`)
            .then(r => r.json())
            .then(entries => {
                const map = {};
                for (const entry of entries) {
                    const parts  = (entry.tags || '').split(',').map(t => t.trim()).filter(Boolean);
                    const gname  = parts[0] || 'Sin grupo';
                    const spname = parts[1] || null;
                    const idea   = entry.summary || entry.content || '';

                    if (!map[gname]) map[gname] = { name: gname, ideas: [], subgroups: {} };
                    if (spname) {
                        if (!map[gname].subgroups[spname]) map[gname].subgroups[spname] = [];
                        if (idea) map[gname].subgroups[spname].push(idea);
                    } else {
                        if (idea) map[gname].ideas.push(idea);
                    }
                }
                const groups = Object.values(map).map(g => ({
                    name:      g.name,
                    ideas:     g.ideas,
                    subgroups: Object.entries(g.subgroups).map(([k, v]) => ({ name: k, ideas: v })),
                }));
                renderMainGrid(groups.slice(0, MAX_BUBBLES));
            })
            .catch(() => renderMainGrid([]));
    }

    // ── Main grid ─────────────────────────────────────────────────────────────
    function renderMainGrid(groups) {
        projectsGrid.innerHTML = '';
        if (!groups.length) {
            projectsGrid.innerHTML =
                '<p style="opacity:.4;text-align:center;grid-column:1/-1">Sin grupos todavía. Escribe tu primera idea arriba.</p>';
            return;
        }
        groups.forEach(group => {
            const el  = document.createElement('div');
            el.classList.add('grid-item');

            const txt = document.createElement('span');
            txt.classList.add('item-text');
            txt.textContent = group.name;
            el.appendChild(txt);

            el.addEventListener('click', () => {
                navStack = [];
                pushDetail(group.name, buildItems(group), 'Grupos');
            });
            projectsGrid.appendChild(el);
        });
    }

    // Convert group/subgroup node to mixed items array
    // Subgroups first (as navigable bubbles), then direct ideas as text rows
    function buildItems(node) {
        const items = [];
        for (const sg of (node.subgroups || [])) {
            items.push({ type: 'subgroup', name: sg.name, ideas: sg.ideas || [] });
        }
        for (const idea of (node.ideas || [])) {
            items.push({ type: 'idea', text: idea });
        }
        return items;
    }

    // ── View switching ────────────────────────────────────────────────────
    function showView(id) {
        projectsView.classList.toggle('active-view', id === 'projects-view');
        projectsView.classList.toggle('hidden-view', id !== 'projects-view');
        detailView.classList.toggle('active-view', id === 'detail-view');
        detailView.classList.toggle('hidden-view', id !== 'detail-view');
        backButton.style.display = (id === 'detail-view') ? 'flex' : 'none';
    }

    // ── Detail view navigation ────────────────────────────────────────────────
    function pushDetail(title, items, parentLabel) {
        navStack.push({ title, items, parentLabel });
        renderDetail();
        showView('detail-view');
    }

    function renderDetail() {
        const frame = navStack[navStack.length - 1];
        detailTitle.textContent = frame.title;
        backLabel.textContent   = frame.parentLabel;
        renderDetailContent(frame.items);
    }

    function renderDetailContent(items) {
        detailContent.innerHTML = '';
        if (!items.length) {
            detailContent.innerHTML = '<p class="detail-empty">Sin contenido todavía.</p>';
            return;
        }
        items.forEach(item => {
            if (item.type === 'subgroup') {
                // ── Sub-bubble ────────────────────────────────────────────────
                const bubble = document.createElement('div');
                bubble.classList.add('sub-bubble');

                const txt = document.createElement('span');
                txt.classList.add('sub-bubble-text');
                txt.textContent = item.name;
                bubble.appendChild(txt);

                if (item.ideas.length) {
                    const cnt = document.createElement('span');
                    cnt.classList.add('sub-bubble-count');
                    cnt.textContent = `${item.ideas.length} idea${item.ideas.length > 1 ? 's' : ''}`;
                    bubble.appendChild(cnt);
                }

                bubble.addEventListener('click', () => {
                    const parentTitle = navStack[navStack.length - 1].title;
                    const subItems    = item.ideas.map(t => ({ type: 'idea', text: t }));
                    pushDetail(item.name, subItems, parentTitle);
                });
                detailContent.appendChild(bubble);
            } else {
                // ── Idea row ──────────────────────────────────────────────────
                const row = document.createElement('div');
                row.classList.add('idea-item');

                const bullet = document.createElement('span');
                bullet.classList.add('idea-bullet');

                const txt = document.createElement('span');
                txt.classList.add('idea-text');
                txt.textContent = item.text;

                row.appendChild(bullet);
                row.appendChild(txt);
                detailContent.appendChild(row);
            }
        });
    }

    backButton.addEventListener('click', () => {
        navStack.pop();
        if (navStack.length === 0) {
            showView('projects-view');
        } else {
            renderDetail();
        }
    });

    // ── Submit note ───────────────────────────────────────────────────────────
    function submitNote(text) {
        if (!text.trim()) return;
        searchInput.disabled    = true;
        searchInput.placeholder = 'Procesando con IA...';

        fetch(`${BACKEND_URL}/note`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ content: text.trim(), origin: 'frontend' }),
        })
        .then(r => r.json())
        .then(dataArr => {
            // El backend devuelve siempre una lista
            const items = Array.isArray(dataArr) ? dataArr : [dataArr];
            const data  = items.find(d => d.action !== 'ignored') || items[0];

            searchInput.value    = '';
            searchInput.disabled = false;

            if (data.action === 'ignored') {
                searchInput.placeholder = '\u26a0\ufe0f La IA no entendi\u00f3 esa nota. Intenta de nuevo.';
            } else if (data.action === 'delete') {
                searchInput.placeholder = `\ud83d\uddd1\ufe0f Eliminado de "${data.group}"`;
            } else {
                const sp    = data.subgroup ? ` \u203a ${data.subgroup}` : '';
                const extra = items.length > 1 ? ` (+${items.length - 1} m\u00e1s)` : '';
                searchInput.placeholder = `\u2713 Guardado en "${data.group}${sp}"${extra}`;
            }
            setTimeout(() => { searchInput.placeholder = 'Buscar o crear grupo/idea...'; }, 3000);
            loadGroups();
        })
        .catch(() => {
            searchInput.disabled    = false;
            searchInput.placeholder = '❌ Error conectando con el servidor';
            setTimeout(() => { searchInput.placeholder = 'Buscar o crear grupo/idea...'; }, 3000);
        });
    }

    searchInput.addEventListener('keydown', e => {
        if (e.key === 'Enter') submitNote(searchInput.value);
    });

    loadGroups();
});
