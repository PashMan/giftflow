const webApp = window.Telegram.WebApp;
const API_BASE = window.location.origin + "/api";
// Обновляем версию, чтобы сбросить старый багованный скрипт
const APP_VERSION = '?v=FIXED_NAMES_V1';

console.log("🚀 App started. API Base:", API_BASE);

webApp.ready();
try { webApp.expand(); webApp.setHeaderColor(webApp.themeParams.bg_color || '#1E1E2D'); } catch (e) {}

// Глобальные переменные
let currentView = 'home';
let santaGameId = null;
let loadedChats = [];
let currentCollectionId = null;
let isCreator = false;
let santaInviteLink = null;
const DEFAULT_IMAGE = "https://cdn-icons-png.flaticon.com/512/9466/9466245.png";

// --- API ---
async function fetchAPI(endpoint, data = {}, showLoader = true) {
    if (showLoader && webApp.MainButton.isVisible) webApp.MainButton.showProgress();
    const userId = webApp.initDataUnsafe?.user?.id;
    let effectiveUserId = userId || 12345;

    // console.log(`📡 ${endpoint}`, data);

    try {
        const response = await fetch(API_BASE + endpoint + APP_VERSION, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chat_id: effectiveUserId, ...data })
        });

        const text = await response.text();
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${text.substring(0, 100)}`);
        }
        
        try {
            const resData = JSON.parse(text);
            if (showLoader && webApp.MainButton.isVisible) webApp.MainButton.hideProgress();
            if (resData.status === 'error') throw new Error(resData.error);
            return resData;
        } catch (e) {
            throw new Error("Invalid JSON response");
        }
    } catch (err) {
        if (showLoader && webApp.MainButton.isVisible) webApp.MainButton.hideProgress();
        console.error("API Error:", err);
        webApp.showAlert(`Ошибка:\n${err.message}`);
        throw err;
    }
}

// --- НАВИГАЦИЯ ---
document.querySelectorAll('.tab-switcher').forEach(btn => {
    btn.addEventListener('click', () => switchView(btn.dataset.view));
});

function switchView(viewName) {
    currentView = viewName;
    
    // Обновляем верхние табы
    document.querySelectorAll('.top-tabs .tab-button').forEach(btn => {
        const btnView = btn.dataset.view;
        let isActive = false;
        if (viewName === 'santa' && btnView === 'santa') isActive = true;
        else if ((viewName === 'home' || viewName === 'mycolls') && btnView === 'home') isActive = true;
        btn.classList.toggle('active', isActive);
    });

    // Обновляем нижнее меню
    document.querySelectorAll('.footer-nav .nav-item').forEach(nav => {
        nav.classList.toggle('active', nav.dataset.view === viewName);
    });

    // Переключаем экраны
    document.querySelectorAll('.main-view').forEach(s => s.style.display = 'none');
    const activeView = document.getElementById(`view-${viewName}`);
    if (activeView) activeView.style.display = 'block';

    // 🔥 ИСПРАВЛЕНО: Вызываем правильные функции загрузки данных
    if (viewName === 'home') loadQuickCollectData();
    if (viewName === 'mycolls') loadMyCollectionsData();
    if (viewName === 'santa') initSanta(); // Было loadSanta, стало initSanta
}


// --- СБОРЫ (ДАННЫЕ) ---

// Загрузка для вкладки "Создать"
function loadQuickCollectData() {
    if (loadedChats.length > 0) return; 
    fetchAPI('/chats').then(data => {
        loadedChats = data.chats || [];
        renderChatSelect();
    });
}

// Загрузка для вкладки "Мои сборы"
function loadMyCollectionsData() {
    fetchAPI('/collections/my').then(data => {
        renderCollections(data.data.created, 'list-created');
        renderCollections(data.data.participated, 'list-participated');
    });
}

function renderChatSelect() {
    const select = document.getElementById('collect-chat-select');
    if (!select) return;
    select.innerHTML = '';
    if (loadedChats.length === 0) {
        select.innerHTML = '<option disabled selected value="">Нет доступных чатов</option>';
    } else {
        loadedChats.forEach(chat => { select.innerHTML += `<option value="${chat.chat_id}">${chat.title}</option>`; });
    }
}

function renderCollections(list, containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '';
    if (!list || list.length === 0) {
        container.innerHTML = '<p class="text-center" style="color: gray;">Пока пусто</p>';
        return;
    }
    list.forEach(c => {
        container.innerHTML += `
            <div class="content-block" onclick="openCollectionDetails('${c.id}')" style="cursor: pointer; margin-bottom: 10px;">
                <div style="display: flex; justify-content: space-between;"><strong>${c.goal}</strong><span style="color: var(--accent-color);">${c.current.toLocaleString()} ₽</span></div>
                <div class="progress-bar" style="margin: 10px 0; background: #333;"><div style="width: ${c.percent}%; height: 100%; background: var(--accent-color); border-radius: 4px;"></div></div>
                <div style="font-size: 12px; color: #aaa; display: flex; justify-content: space-between;"><span>${c.amount.toLocaleString()} ₽</span><span>${c.percent}%</span></div>
            </div>`;
    });
}

// --- ДЕЙСТВИЯ СБОРОВ ---
window.sendQuickCollect = function() {
    const chatId = document.getElementById('collect-chat-select').value;
    const amount = document.getElementById('collect-amount').value;
    const goal = document.getElementById('collect-goal').value;
    if (!chatId) return webApp.showAlert("Выберите чат!");
    if (!amount || amount <= 0) return webApp.showAlert("Укажите сумму!");
    if (!goal) return webApp.showAlert("Укажите цель!");
    fetchAPI('/collections/create', { target_chat_id: chatId, amount: amount, goal: goal }).then(() => {
        webApp.showAlert("Сбор создан!");
        document.getElementById('collect-amount').value = '';
        document.getElementById('collect-goal').value = '';
        switchView('mycolls');
    });
}


// --- САНТА (ЛОГИКА) ---

// 🔥 ИСПРАВЛЕНО: Функция теперь называется initSanta, как и вызывается в switchView
async function initSanta() {
    try {
        const data = await fetchAPI('/santa/state');
        hideSantaScreens();
        
        // В data.state теперь список игр, но для совместимости со старым UI 
        // пока берем первую активную или показываем старт, если список пуст.
        // (В main.py мы вернули get_user_santa_state к возврату одной игры? 
        //  НЕТ, в последнем main.py get_user_santa_state возвращает объект state, а не список игр!
        //  Проверим main.py... Да, там возвращается `state` одной игры. Всё ок.)
        
        if (!data.state) { 
            document.getElementById('santa-start-screen').style.display = 'block'; 
        } else {
            santaGameId = data.state.game_id;
            santaInviteLink = data.state.invite_link;
            
            if (data.state.game_status === 'recruiting') showSantaLobby(data.state);
            else if (data.state.game_status === 'active') showSantaGame(data.state);
        }
    } catch (e) {
        // Ошибки уже обработаны в fetchAPI
    }
}

function hideSantaScreens() {
    document.getElementById('santa-start-screen').style.display = 'none';
    document.getElementById('santa-lobby-screen').style.display = 'none';
    document.getElementById('santa-game-screen').style.display = 'none';
}

function showSantaLobby(state) {
    document.getElementById('santa-lobby-screen').style.display = 'block';
    document.getElementById('santa-game-title').textContent = state.game_title;
    document.getElementById('santa-participants-count').textContent = state.participants_count;
    
    const list = document.getElementById('santa-participants-list'); 
    list.innerHTML = '';
    if(state.participants_list && state.participants_list.length > 0) {
        state.participants_list.forEach(name => { 
            list.innerHTML += `<li style="margin-bottom: 5px;"><i class="fas fa-user-circle" style="color: #aaa; margin-right: 8px;"></i> ${name}</li>`; 
        });
    } else {
        list.innerHTML = '<li style="color: gray;">Пока никого...</li>';
    }

    document.getElementById('santa-wishlist').value = state.my_wishlist || '';
    document.getElementById('santa-admin-controls').style.display = state.is_creator ? 'block' : 'none';
}

function showSantaGame(state) {
    document.getElementById('santa-game-screen').style.display = 'block';
    document.getElementById('santa-target-name').textContent = state.target_user_name || "Участник";
    document.getElementById('santa-target-wishlist').innerHTML = renderWishlistText(state.target_wishlist);
    document.getElementById('santa-status-buttons').style.display = 'flex';
}

// --- ДЕЙСТВИЯ САНТЫ ---
window.createSantaGame = function() { fetchAPI('/santa/create', { title: "Тайный Санта" }).then(initSanta); }
window.saveSantaWishlist = function() { const w = document.getElementById('santa-wishlist').value; if(!w) return webApp.showAlert("Пусто!"); fetchAPI('/santa/join', { game_id: santaGameId, wishlist: w }).then(() => { webApp.showAlert("Сохранено!"); initSanta(); }); }
window.startSantaGame = function() { webApp.showConfirm("Начать жеребьевку?", (ok) => { if(ok && santaGameId) fetchAPI('/santa/start', { game_id: santaGameId }).then(initSanta); }); }
window.shareSantaLink = function() { 
    if(santaInviteLink) webApp.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(santaInviteLink)}&text=Го в Санту!`); 
    else webApp.showAlert("Ссылка не найдена");
}
window.markGiftSent = function() { webApp.showConfirm("Отправили?", (ok) => { if(ok) fetchAPI('/santa/sent', { game_id: santaGameId }).then(() => webApp.showAlert("Ок!")); }); }
window.markGiftReceived = function() { webApp.showConfirm("Получили?", (ok) => { if(ok) fetchAPI('/santa/received', { game_id: santaGameId }).then(() => webApp.showAlert("Супер!")); }); }


// --- ДЕТАЛИ СБОРА ---
window.openCollectionDetails = function(id) {
    currentCollectionId = String(id);
    document.getElementById('collection-details').style.display = 'block';
    document.getElementById('view-controls').style.display = 'block'; document.getElementById('edit-controls').style.display = 'none';
    document.getElementById('detail-goal').textContent = "Загрузка..."; document.getElementById('detail-img').src = DEFAULT_IMAGE;
    
    const userId = webApp.initDataUnsafe?.user?.id;
    
    fetchAPI('/collections/info', { collection_id: currentCollectionId }).then(data => {
        const item = data.data;
        document.getElementById('detail-goal').textContent = item.goal; document.getElementById('detail-desc').textContent = item.description || "Нет описания";
        document.getElementById('detail-current').textContent = `${item.current.toLocaleString()} ₽`; document.getElementById('detail-total').textContent = `из ${item.amount.toLocaleString()} ₽`;
        document.getElementById('detail-progress').style.width = `${item.percent}%`;
        let img = item.image_url || DEFAULT_IMAGE; if(img !== DEFAULT_IMAGE) img += "?t=" + Date.now();
        document.getElementById('detail-img').src = img; document.getElementById('detail-img-url-hidden').value = item.image_url || DEFAULT_IMAGE;
        
        isCreator = String(item.creator_id) === String(userId);
        document.getElementById('edit-btn-container').style.display = isCreator ? 'block' : 'none';
        
        if (item.status === 'finished') { document.getElementById('payment-control-block').style.display = 'none'; document.getElementById('finished-message').style.display = 'block'; } else { document.getElementById('payment-control-block').style.display = 'block'; document.getElementById('finished-message').style.display = 'none'; }
    });
}
window.closeDetails = function() { document.getElementById('collection-details').style.display = 'none'; }
window.enableEditMode = function() { if(!isCreator) return; document.getElementById('view-controls').style.display='none'; document.getElementById('edit-controls').style.display='block'; document.getElementById('detail-desc-input').value = document.getElementById('detail-desc').textContent; }
window.saveChanges = function() { const d = document.getElementById('detail-desc-input').value; const i = document.getElementById('detail-img-url-hidden').value; fetchAPI('/collections/update', { collection_id: currentCollectionId, description: d, image_url: i }).then(() => { webApp.showAlert("Сохранено!"); closeDetails(); loadMyCollectionsData(); }); }

window.deleteCollection = function() {
    webApp.showConfirm("Удалить сбор?", (ok) => {
        if(ok) fetchAPI('/collections/delete', { collection_id: currentCollectionId }).then(() => { webApp.showAlert("Удалено"); closeDetails(); loadMyCollectionsData(); });
    });
}

document.addEventListener('DOMContentLoaded', () => { const fi = document.getElementById('image-upload-input'); if(fi) fi.addEventListener('change', () => { const f = fi.files[0]; if(!f) return; const st = document.getElementById('upload-status'); st.textContent = "Загрузка..."; const fd = new FormData(); fd.append('image', f); fetch("/api/upload", { method: 'POST', body: fd }).then(r=>r.json()).then(d=>{ if(d.status==='ok') { document.getElementById('detail-img-url-hidden').value = d.url; st.textContent = "✅ ОК"; } else st.textContent = "Ошибка"; }).catch(()=>st.textContent="Ошибка"); }); });
window.initiatePayment = function() { const amount = parseInt(document.getElementById('contribute-input').value); if(!amount || amount <= 0) return webApp.showAlert("Некорректная сумма!"); fetchAPI('/collections/invoice', { collection_id: currentCollectionId, amount: amount }).then(d => { if(d.invoice_url) webApp.openInvoice(d.invoice_url, (s) => { if(s==='paid') { webApp.showAlert("Оплачено!"); closeDetails(); setTimeout(loadMyCollectionsData, 1500); } }); else webApp.showAlert("Ошибка инвойса"); }); }

function renderWishlistText(text) { if (!text) return "Вишлист пуст"; let safeText = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); const markdownLinkRegex = /\[([^\]]+)\]\(([^)]+)\)/g; safeText = safeText.replace(markdownLinkRegex, (m, txt, url) => `<a href="${url}" target="_blank" class="wishlist-link"><i class="fas fa-external-link-alt"></i> ${txt}</a>`); return safeText.replace(/\n/g, '<br>'); }

// --- INIT ---
function initApp() {
    const p = new URLSearchParams(window.location.search);
    const start = p.get('tgWebAppStartParam');
    if (start) {
        if (start.startsWith('donate_')) { openCollectionDetails(start.split('_')[1]); return; }
        else if (start.startsWith('santa_')) { santaGameId = start.split('_')[1]; fetchAPI('/santa/join', { game_id: santaGameId, wishlist: "" }).then(() => switchView('santa')); return; }
    }
    // По умолчанию - "Создать"
    switchView('home');
}
initApp();
