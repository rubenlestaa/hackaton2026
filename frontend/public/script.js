// Backend URL — change to match your environment
const BACKEND_URL = 'http://localhost:8000';
const AI_URL      = 'http://localhost:8001';

// Translation cache: originalText → EN text (persists for the session)
const transCache = {};

async function translateBatch(strings) {
    const unique = [...new Set(strings.filter(s => s && !transCache[s]))];
    if (!unique.length) return;
    try {
        const resp = await fetch(`${AI_URL}/translate`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ texts: unique, target_lang: 'en' }),
        });
        const { translations } = await resp.json();
        unique.forEach((s, i) => { if (translations[i]) transCache[s] = translations[i]; });
    } catch { /* silent — fall back to originals */ }
}

async function maybeTranslate(groups) {
    if (currentLang !== 'en') return groups;
    // Collect every string that needs translating
    const strs = [];
    groups.forEach(g => {
        strs.push(g.name);
        (g.ideas || []).forEach(i => strs.push(i));
        (g.subgroups || []).forEach(sg => {
            strs.push(sg.name);
            (sg.ideas || []).forEach(i => strs.push(i));
        });
    });
    Object.values(summariesMap).forEach(s => strs.push(s));
    await translateBatch(strs);
    const tr = s => (s && transCache[s]) ? transCache[s] : s;
    return groups.map(g => ({
        _orig:     g.name,
        name:      tr(g.name),
        ideas:     (g.ideas || []).map(tr),
        subgroups: (g.subgroups || []).map(sg => ({
            _orig: sg.name,
            name:  tr(sg.name),
            ideas: (sg.ideas || []).map(tr),
        })),
    }));
}

// Language state: 'es' | 'en'
let currentLang = localStorage.getItem('brain_lang') || 'es';

// ── UI strings (ES / EN) ─────────────────────────────────────────────────────
const STRINGS = {
    es: {
        remindersTitle:    'Alertas pendientes',
        searchPlaceholder: 'Buscar o crear grupo/idea...',
        emptyGroups:       'Sin grupos todavía. Escribe tu primera idea arriba.',
        emptyDetail:       'Sin contenido todavía.',
        summaryLabel:      '✨ Resumen IA',
        groupsLabel:       'Grupos',
        processing:        'Procesando con IA...',
        ignored:           '⚠️ La IA no entendió esa nota. Intenta de nuevo.',
        deleted:           '🗑️ Eliminado de',
        saved:             '✓ Guardado en',
        more:              'más',
        errorServer:       '❌ Error conectando con el servidor',
        recording:         '🎤 Grabando... toca de nuevo para parar',
        transcribing:      'Transcribiendo audio con IA...',
        noMic:             '❌ El navegador no permite el micro en HTTP. Prueba en Chrome con localhost.',
        requestingMic:     'Solicitando permiso de micrófono...',
        noSpeech:          '⚠️ No se detectó habla. Intenta de nuevo.',
        transcribeError:   '❌ Error al transcribir el audio',
        micError:          '❌ No se pudo acceder al micrófono: ',
        reminderSet:       '⏰ Recordatorio guardado para',
    },
    en: {
        remindersTitle:    'Pending alerts',
        searchPlaceholder: 'Search or create group / idea...',
        emptyGroups:       'No groups yet. Write your first idea above.',
        emptyDetail:       'No content yet.',
        summaryLabel:      '✨ AI Summary',
        groupsLabel:       'Groups',
        processing:        'Processing with AI...',
        ignored:           '⚠️ The AI didn\'t understand that note. Try again.',
        deleted:           '🗑️ Deleted from',
        saved:             '✓ Saved in',
        more:              'more',
        errorServer:       '❌ Error connecting to server',
        recording:         '🎤 Recording... tap again to stop',
        transcribing:      'Transcribing audio with AI...',
        noMic:             '❌ Browser doesn\'t allow mic on HTTP. Try Chrome on localhost.',
        requestingMic:     'Requesting microphone permission...',
        noSpeech:          '⚠️ No speech detected. Try again.',
        transcribeError:   '❌ Error transcribing the audio',
        micError:          '❌ Could not access microphone: ',
        reminderSet:       '⏰ Reminder set for',
    },
};
function t(key) { return (STRINGS[currentLang] || STRINGS.es)[key] || STRINGS.es[key]; }
const MAX_BUBBLES = 12;

// ── Pin state (persisted in localStorage) ────────────────────────────────────
// { groupName: timestamp }  — lower timestamp = pinned first
let pinnedGroups = JSON.parse(localStorage.getItem('brain_pins') || '{}');

function savePin(name) {
    pinnedGroups[name] = pinnedGroups[name] || Date.now();
    localStorage.setItem('brain_pins', JSON.stringify(pinnedGroups));
}
function removePin(name) {
    delete pinnedGroups[name];
    localStorage.setItem('brain_pins', JSON.stringify(pinnedGroups));
}
function isPinned(name) { return Object.prototype.hasOwnProperty.call(pinnedGroups, name); }

function sortByPin(groups) {
    const pinned   = groups.filter(g => isPinned(g.name))
                           .sort((a, b) => pinnedGroups[a.name] - pinnedGroups[b.name]);
    const unpinned = groups.filter(g => !isPinned(g.name));
    return [...pinned, ...unpinned];
}

// Cache of summaries keyed by "group" or "group/subgroup"
let summariesMap = {};

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
    const langToggle    = document.getElementById('lang-toggle');

    // ── Language toggle ───────────────────────────────────────────────────────
    langToggle.textContent = currentLang.toUpperCase();
    if (currentLang === 'en') langToggle.classList.add('active-en');
    langToggle.addEventListener('click', () => {
        currentLang = currentLang === 'es' ? 'en' : 'es';
        localStorage.setItem('brain_lang', currentLang);
        langToggle.textContent = currentLang.toUpperCase();
        langToggle.classList.toggle('active-en', currentLang === 'en');
        searchInput.placeholder = t('searchPlaceholder');
        loadGroups();
        loadReminders();
    });

    // ── Load groups from backend ──────────────────────────────────────────────
    function loadGroups() {
        // Fetch entries and summaries in parallel
        Promise.all([
            fetch(`${BACKEND_URL}/inbox?status=processed`).then(r => r.json()),
            fetch(`${BACKEND_URL}/summaries`).then(r => r.json()).catch(() => []),
        ]).then(([entries, summaries]) => {
            // Build summaries map: "group" or "group/subgroup" → summary text
            summariesMap = {};
            for (const s of (summaries || [])) {
                const key = s.subgroup ? `${s.group}/${s.subgroup}` : s.group;
                summariesMap[key] = s.summary;
            }

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
            return maybeTranslate(groups.slice(0, MAX_BUBBLES));
        }).then(tGroups => renderMainGrid(tGroups))
        .catch(() => renderMainGrid([]));
    }

    // ── Reminders ─────────────────────────────────────────────────────────────
    function loadReminders() {
        fetch(`${BACKEND_URL}/reminders?sent=false`)
            .then(r => r.json())
            .then(renderReminders)
            .catch(() => renderReminders([]));
    }

    async function renderReminders(reminders) {
        const section = document.getElementById('reminders-section');
        const list    = document.getElementById('reminders-list');
        const title   = document.getElementById('reminders-title');
        title.textContent = t('remindersTitle');
        if (!reminders.length) { section.style.display = 'none'; return; }
        if (currentLang === 'en') {
            await translateBatch(reminders.map(r => r.message));
        }
        section.style.display = 'block';
        list.innerHTML = '';
        reminders.forEach(r => {
            const msg = (currentLang === 'en' && transCache[r.message]) ? transCache[r.message] : r.message;
            const fireDate = new Date(r.fire_at);
            const timeStr  = fireDate.toLocaleString(
                currentLang === 'en' ? 'en-GB' : 'es-ES',
                { weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }
            );
            const item = document.createElement('div');
            item.className = 'reminder-item';
            item.innerHTML = `<span class="reminder-time">${timeStr}</span><span class="reminder-msg">${msg}</span>`;
            list.appendChild(item);
        });
    }

    // ── Context menu (pin/unpin) ──────────────────────────────────────────────
    let _ctxMenu = null;
    function closeCtxMenu() {
        if (_ctxMenu) { _ctxMenu.remove(); _ctxMenu = null; }
    }
    document.addEventListener('click', closeCtxMenu);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeCtxMenu(); });

    function showPinMenu(e, groupName) {
        e.preventDefault();
        closeCtxMenu();
        const menu = document.createElement('div');
        menu.className = 'pin-context-menu';
        const pinned = isPinned(groupName);
        menu.innerHTML = `<button class="pin-menu-item">${pinned ? '\uD83D\uDCCC Desanclar' : '\uD83D\uDCCC Anclar'}</button>`;
        menu.style.left = `${e.clientX}px`;
        menu.style.top  = `${e.clientY}px`;
        menu.querySelector('button').addEventListener('click', ev => {
            ev.stopPropagation();
            if (pinned) removePin(groupName); else savePin(groupName);
            closeCtxMenu();
            loadGroups();
        });
        document.body.appendChild(menu);
        _ctxMenu = menu;
        // Keep inside viewport
        const r = menu.getBoundingClientRect();
        if (r.right  > window.innerWidth)  menu.style.left = `${e.clientX - r.width}px`;
        if (r.bottom > window.innerHeight) menu.style.top  = `${e.clientY - r.height}px`;
    }

    // ── Main grid ─────────────────────────────────────────────────────────────
    function renderMainGrid(groups) {
        projectsGrid.innerHTML = '';
        if (!groups.length) {
            projectsGrid.innerHTML =
                `<p style="opacity:.4;text-align:center;grid-column:1/-1">${t('emptyGroups')}</p>`;
            return;
        }
        sortByPin(groups).slice(0, MAX_BUBBLES).forEach(group => {
            const el  = document.createElement('div');
            el.classList.add('grid-item');
            if (isPinned(group.name)) el.classList.add('grid-item--pinned');

            const txt = document.createElement('span');
            txt.classList.add('item-text');
            txt.textContent = group.name;
            el.appendChild(txt);

            if (isPinned(group.name)) {
                const pin = document.createElement('span');
                pin.className = 'pin-badge';
                pin.textContent = '\uD83D\uDCCC';
                el.appendChild(pin);
            }

            el.addEventListener('click', () => {
                navStack = [];
                const origName   = group._orig || group.name;
                const rawSummary = summariesMap[origName] || null;
                const summary    = rawSummary ? (transCache[rawSummary] || rawSummary) : null;
                pushDetail(group.name, buildItems(group), t('groupsLabel'), summary, origName);
            });
            el.addEventListener('contextmenu', e => showPinMenu(e, group.name));
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
    function pushDetail(title, items, parentLabel, summary, origTitle) {
        navStack.push({ title, _orig: origTitle || title, items, parentLabel, summary: summary || null });
        renderDetail();
        showView('detail-view');
    }

    function renderDetail() {
        const frame = navStack[navStack.length - 1];
        detailTitle.textContent = frame.title;
        backLabel.textContent   = frame.parentLabel;
        renderDetailContent(frame.items, frame.summary);
    }

    function renderDetailContent(items, summary) {
        detailContent.innerHTML = '';

        // Summary card (only when AI has generated one)
        if (summary) {
            const card = document.createElement('div');
            card.classList.add('summary-card');
            const label = document.createElement('span');
            label.classList.add('summary-card-label');
            label.textContent = t('summaryLabel');
            const text = document.createElement('p');
            text.classList.add('summary-card-text');
            text.textContent = summary;
            card.appendChild(label);
            card.appendChild(text);
            detailContent.appendChild(card);
        }

        if (!items.length) {
            detailContent.innerHTML = `<p class="detail-empty">${t('emptyDetail')}</p>`;
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
                    const parentFrame = navStack[navStack.length - 1];
                    const parentOrig  = parentFrame._orig || parentFrame.title;
                    const itemOrig    = item._orig  || item.name;
                    const subItems    = item.ideas.map(idea => ({ type: 'idea', text: idea }));
                    const rawSummary  = summariesMap[`${parentOrig}/${itemOrig}`] || null;
                    const subSummary  = rawSummary ? (transCache[rawSummary] || rawSummary) : null;
                    pushDetail(item.name, subItems, parentFrame.title, subSummary, itemOrig);
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
    const sendButton = document.getElementById('send-button');

    function setLoading(on) {
        searchInput.disabled = on;
        sendButton.disabled  = on;
        sendButton.classList.toggle('is-loading', on);
        if (on) searchInput.placeholder = t('processing');
    }

    function submitNote(text) {
        if (!text.trim()) return;
        setLoading(true);

        fetch(`${BACKEND_URL}/note`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ content: text.trim(), origin: 'frontend', lang: currentLang }),
        })
        .then(r => r.json())
        .then(dataArr => {
            // El backend devuelve siempre una lista
            const items = Array.isArray(dataArr) ? dataArr : [dataArr];
            const data  = items.find(d => d.action !== 'ignored') || items[0];

            searchInput.value = '';
            setLoading(false);

            if (data.action === 'ignored') {
                searchInput.placeholder = t('ignored');
            } else if (data.action === 'delete') {
                const n = data.deleted_count || 1;
                let scope;
                if (data.idea) {
                    scope = currentLang === 'en'
                        ? `"${data.idea}" from "${data.group}"`
                        : `"${data.idea}" de "${data.group}"`;
                } else if (data.subgroup) {
                    scope = currentLang === 'en'
                        ? `subgroup "${data.subgroup}" of "${data.group}"`
                        : `subgrupo "${data.subgroup}" de "${data.group}"`;
                } else {
                    scope = currentLang === 'en'
                        ? `group "${data.group}"`
                        : `grupo "${data.group}"`;
                }
                const plural = currentLang === 'en'
                    ? (n === 1 ? `${n} item` : `${n} items`)
                    : (n === 1 ? `${n} idea` : `${n} ideas`);
                searchInput.placeholder = `🗑️ ${currentLang === 'en' ? 'Deleted' : 'Eliminado'} ${scope} (${plural})`;
            } else if (data.action === 'remind') {
                const when = data.remind_at
                    ? new Date(data.remind_at).toLocaleString(
                        currentLang === 'en' ? 'en-GB' : 'es-ES',
                        { weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }
                      )
                    : '';
                searchInput.placeholder = `${t('reminderSet')} ${when} — ${data.idea}`;
                loadReminders();
            } else {
                const sp    = data.subgroup ? ` › ${data.subgroup}` : '';
                const extra = items.length > 1 ? ` (+${items.length - 1} ${t('more')})` : '';
                searchInput.placeholder = `${t('saved')} "${data.group}${sp}"${extra}`;
            }
            setTimeout(() => { searchInput.placeholder = t('searchPlaceholder'); }, 3000);
            loadGroups();
        })
        .catch(() => {
            setLoading(false);
            searchInput.placeholder = t('errorServer');
            setTimeout(() => { searchInput.placeholder = t('searchPlaceholder'); }, 3000);
        });
    }

    document.getElementById('send-button').addEventListener('click', () => {
        submitNote(searchInput.value);
    });

    searchInput.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !searchInput.disabled) submitNote(searchInput.value);
    });

    // ── Microphone recording ──────────────────────────────────────────────────
    const micButton  = document.getElementById('mic-button');
    let mediaRecorder = null;
    let audioChunks   = [];

    function setMicState(state) {
        micButton.classList.toggle('is-recording',    state === 'recording');
        micButton.classList.toggle('is-transcribing', state === 'transcribing');
        micButton.disabled    = (state === 'transcribing');
        sendButton.disabled   = (state === 'recording' || state === 'transcribing');
        searchInput.disabled  = (state === 'transcribing');
        if (state === 'recording')    searchInput.placeholder = t('recording');
        if (state === 'transcribing') searchInput.placeholder = t('transcribing');
    }

    micButton.addEventListener('click', async () => {
        if (mediaRecorder && mediaRecorder.state === 'recording') {
            mediaRecorder.stop();
            return;
        }

        // Check secure context / API availability
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            searchInput.placeholder = t('noMic');
            setTimeout(() => { searchInput.placeholder = t('searchPlaceholder'); }, 4000);
            return;
        }

        // Give immediate visual feedback before async permission prompt
        micButton.classList.add('is-recording');
        searchInput.placeholder = t('requestingMic');

        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            audioChunks  = [];
            const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                ? 'audio/webm;codecs=opus'
                : MediaRecorder.isTypeSupported('audio/webm')
                    ? 'audio/webm'
                    : '';
            mediaRecorder = mimeType
                ? new MediaRecorder(stream, { mimeType })
                : new MediaRecorder(stream);

            mediaRecorder.addEventListener('dataavailable', e => {
                if (e.data.size > 0) audioChunks.push(e.data);
            });

            mediaRecorder.addEventListener('stop', async () => {
                stream.getTracks().forEach(t => t.stop());
                setMicState('transcribing');
                const blob     = new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
                const formData = new FormData();
                formData.append('audio', blob, 'recording.webm');
                try {
                    const resp = await fetch(`${BACKEND_URL}/transcribe`, { method: 'POST', body: formData });
                    if (!resp.ok) throw new Error('transcription error');
                    const { transcribed_text } = await resp.json();
                    setMicState('idle');
                    if (transcribed_text && transcribed_text.trim()) {
                        submitNote(transcribed_text.trim());
                    } else {
                        searchInput.placeholder = t('noSpeech');
                        setTimeout(() => { searchInput.placeholder = t('searchPlaceholder'); }, 3000);
                    }
                } catch {
                    setMicState('idle');
                    searchInput.placeholder = t('transcribeError');
                    setTimeout(() => { searchInput.placeholder = t('searchPlaceholder'); }, 3000);
                }
            });

            mediaRecorder.start();
            setMicState('recording');
        } catch (err) {
            micButton.classList.remove('is-recording');
            searchInput.placeholder = t('micError') + (err.message || err);
            setTimeout(() => { searchInput.placeholder = t('searchPlaceholder'); }, 4000);
        }
    });

    loadGroups();
    loadReminders();
    // Poll reminders every 30 s to reflect emails sent by the scheduler
    setInterval(loadReminders, 30000);
});
