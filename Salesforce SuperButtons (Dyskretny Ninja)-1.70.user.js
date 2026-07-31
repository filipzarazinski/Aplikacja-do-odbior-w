// ==UserScript==
// @name         Salesforce SuperButtons (Dyskretny Ninja)
// @namespace    http://tampermonkey.net/
// @version      1.70
// @description  Wypełnianie w tle z działającą logiką z v1.60. Ukrywanie modali + mały dymek (Toast).
// @match        *://*.lightning.force.com/*
// @grant        none
// ==/UserScript==

(function () {
    'use strict';

    const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

    // =========================================================================
    // 1. GHOST MODE & TOAST (Mały dymek w rogu + agresywne ukrywanie okien)
    // =========================================================================
    const style = document.createElement('style');
    style.innerHTML = `
        /* Ekstremalnie agresywne ukrywanie modali Salesforce */
        body.sf-stealth-mode .forceModal,
        body.sf-stealth-mode section[role="dialog"],
        body.sf-stealth-mode .slds-modal,
        body.sf-stealth-mode .slds-backdrop,
        body.sf-stealth-mode .uiModal,
        body.sf-stealth-mode div[data-aura-class="uiModal"],
        body.sf-stealth-mode .panel.uiPanel {
            opacity: 0 !important;
            visibility: hidden !important;
            pointer-events: none !important;
            transform: scale(0) !important;
            transition: none !important;
        }

        /* Dyskretny dymek w lewym dolnym rogu (jak we Framelogic) */
        #sf-ninja-toast {
            position: fixed; bottom: 20px; left: 20px;
            background: #1a237e; color: #fff; padding: 12px 24px; border-radius: 8px;
            font-family: 'Salesforce Sans', sans-serif; font-size: 14px; font-weight: bold;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3); z-index: 9999999;
            display: flex; align-items: center; gap: 10px;
            transform: translateY(100px); opacity: 0; pointer-events: none;
            transition: transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275), opacity 0.3s;
        }
        #sf-ninja-toast.show { transform: translateY(0); opacity: 1; }
        #sf-ninja-toast.success { background: #04844b; }
        #sf-ninja-toast.error { background: #c23934; }

        .sf-spinner {
            width: 16px; height: 16px; border: 2px solid rgba(255,255,255,0.3);
            border-top: 2px solid #fff; border-radius: 50%; animation: sf-spin 1s linear infinite;
        }
        @keyframes sf-spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    `;
    document.head.appendChild(style);

    const toastEl = document.createElement('div');
    toastEl.id = 'sf-ninja-toast';
    document.body.appendChild(toastEl);

    function showToast(message, type = 'loading') {
        toastEl.className = 'show';
        if (type === 'loading') {
            toastEl.innerHTML = `<div class="sf-spinner"></div> <span>${message}</span>`;
        } else if (type === 'success') {
            toastEl.classList.add('success');
            toastEl.innerHTML = `<span>✔️ ${message}</span>`;
            setTimeout(() => { toastEl.className = ''; }, 2500);
        } else if (type === 'error') {
            toastEl.classList.add('error');
            toastEl.innerHTML = `<span>❌ ${message}</span>`;
            setTimeout(() => { toastEl.className = ''; }, 4500);
        }
    }

    function setStealthMode(msg) {
        document.body.classList.add('sf-stealth-mode');
        showToast(msg, 'loading');
    }

    function removeStealthMode(msg, isError = false) {
        // Małe opóźnienie na zdjęcie peleryny, żeby okienko zdążyło zniknąć
        setTimeout(() => document.body.classList.remove('sf-stealth-mode'), 200);
        if (msg) showToast(msg, isError ? 'error' : 'success');
        else toastEl.className = '';
    }

    // =========================================================================
    // 2. FUNKCJE NARZĘDZIOWE (DOM TRAVERSAL)
    // =========================================================================
    function getActiveTabRoot() {
        const rootContainer = document.querySelector('div.split-right[role="main"]');
        if(!rootContainer) return null;
        return Array.from(rootContainer.children).find(child =>
            child.matches?.('section.tabContent.active.oneConsoleTab[role="tabpanel"]')
        );
    }

    function findElementContainingTextDeep(text, root) {
        const seen = new Set();
        let foundOnce = null;
        function search(el) {
            if (!el || seen.has(el)) return false;
            seen.add(el);
            for (const node of el.childNodes) {
                if (node.nodeType === Node.TEXT_NODE && node.textContent.trim().includes(text)) {
                    foundOnce = el; return true;
                }
            }
            for (const child of el.children) { if (search(child)) return true; }
            if (el.shadowRoot) {
                for (const shadowChild of el.shadowRoot.children) { if (search(shadowChild)) return true; }
            }
            return false;
        }
        search(root);
        return foundOnce;
    }

    function findSpanByTitleDeep(title, root) {
        function search(el) {
            if (!el) return null;
            if (el.tagName === 'SPAN' && el.getAttribute('title') === title) return el;
            for (const child of el.children) { const res = search(child); if (res) return res; }
            if (el.shadowRoot) {
                for (const child of el.shadowRoot.children) { const res = search(child); if (res) return res; }
            }
            return null;
        }
        return search(root);
    }

    function clickElementSafe(el) {
        if (!el) return false;
        try {
            el.click(); return true;
        } catch (e) { return false; }
    }

    function clickButtonByNameDeep(name = "SaveEdit", root) {
        function search(el) {
            if (!el) return false;
            if (el.tagName === 'BUTTON' && el.getAttribute('name') === name) {
                setTimeout(() => el.click(), 100);
                return true;
            }
            for (const child of el.children) { if (search(child)) return true; }
            if (el.shadowRoot) {
                for (const child of el.shadowRoot.children) { if (search(child)) return true; }
            }
            return false;
        }
        return search(root);
    }

    function findElementInShadowDOM({ root = getActiveTabRoot(), ariaLabel = '', spanToClick = '' } = {}) {
        function search(el) {
            if (!el) return null;
            if (el.getAttribute && el.getAttribute('aria-label') === ariaLabel) {
                clickElementSafe(el);
                if (spanToClick) {
                    setTimeout(() => {
                        const span = findSpanByTitleDeep(spanToClick, root);
                        if (span) clickElementSafe(span);
                    }, 100);
                }
                return el;
            }
            for (const child of el.children) { const found = search(child); if (found) return found; }
            if (el.shadowRoot) {
                for (const child of el.shadowRoot.children) { const found = search(child); if (found) return found; }
            }
            return null;
        }
        return search(root);
    }

    function deepSearch(el, className) {
        const seen2 = new Set();
        function dfs(e) {
            if (!e || seen2.has(e)) return null;
            seen2.add(e);
            if (e.classList && e.classList.contains(className)) return e;
            for (const c of e.children) { const r = dfs(c); if (r) return r; }
            if (e.shadowRoot) {
                for (const sc of e.shadowRoot.children) { const r = dfs(sc); if (r) return r; }
            }
            return null;
        }
        return dfs(el);
    }

    function deepSearchMultipleClasses(el, classArray) {
        const seen2 = new Set();
        function dfs(e) {
            if (!e || seen2.has(e)) return null;
            seen2.add(e);
            if (e.classList && classArray.every(c => e.classList.contains(c))) return e;
            for (const c of e.children) { const r = dfs(c); if (r) return r; }
            if (e.shadowRoot) {
                for (const sc of e.shadowRoot.children) { const r = dfs(sc); if (r) return r; }
            }
            return null;
        }
        return dfs(el);
    }

    function reallySimulateClick(el) {
        if (!el) return;
        el.focus();
        const events = [
            new PointerEvent("pointerdown", { bubbles: true }),
            new MouseEvent("mousedown", { bubbles: true }),
            new PointerEvent("pointerup", { bubbles: true }),
            new MouseEvent("mouseup", { bubbles: true }),
            new MouseEvent("click", { bubbles: true })
        ];
        for (const evt of events) el.dispatchEvent(evt);
    }

    // =========================================================================
    // 3. GŁÓWNA LOGIKA AKCJI (SPRAWDZONA, DZIAŁAJĄCA)
    // =========================================================================

    async function closeCase(option) {
        const root = getActiveTabRoot();
        if (!root) return null;

        setStealthMode(`Zamykanie: ${option}...`);

        try {
            const seen = new Set();
            let pathContainer = null;
            function searchContainer(el) {
                if (!el || seen.has(el)) return false;
                seen.add(el);
                if (el.classList && el.classList.contains("runtime_sales_pathassistantPathAssistantTabSet")) {
                    pathContainer = el; return true;
                }
                for (const child of el.children) { if (searchContainer(child)) return true; }
                if (el.shadowRoot) {
                    for (const shadowChild of el.shadowRoot.children) { if (searchContainer(shadowChild)) return true; }
                }
                return false;
            }
            searchContainer(root);
            if (!pathContainer) throw new Error("Brak PathContainer");

            function findPathNav(el) {
                const seen2 = new Set();
                let found = null;
                function dfs(e) {
                    if (!e || seen2.has(e)) return false;
                    seen2.add(e);
                    if (e.tagName === "UL" && e.classList.contains("slds-path__nav")) { found = e; return true; }
                    for (const c of e.children) { if (dfs(c)) return true; }
                    if (e.shadowRoot) { for (const sc of e.shadowRoot.children) { if (dfs(sc)) return true; } }
                    return false;
                }
                dfs(el);
                return found;
            }

            const pathNav = findPathNav(pathContainer);
            if (!pathNav) throw new Error("Brak pathNav");

            const liEls = pathNav.querySelectorAll("li");
            const lastLi = liEls[liEls.length - 1];
            const aTag = lastLi.querySelector("a");
            if (aTag) aTag.click();

            await delay(250);

            let headerSibling = null;
            if (pathContainer.parentElement) {
                for (const sib of pathContainer.parentElement.children) {
                    if (sib !== pathContainer && sib.classList.contains("runtime_sales_pathassistantPathAssistantHeader")) {
                        headerSibling = sib; break;
                    }
                }
            }
            if (headerSibling) {
                const btn = headerSibling.querySelector("button");
                if (btn) btn.click();
            }

            await delay(250);

            const foundStepClosed = deepSearch(document.body, "runtime_sales_pathassistantPathAssistantStepClosed");
            if (foundStepClosed) {
                const selectEl = deepSearchMultipleClasses(document.body, ['stepAction', 'required-field', 'select']);
                if (selectEl && selectEl.tagName === "SELECT") {
                    await delay(150);
                    selectEl.focus();
                    selectEl.value = option;
                    selectEl.dispatchEvent(new Event("input", { bubbles: true }));
                    selectEl.dispatchEvent(new Event("change", { bubbles: true }));

                    await delay(250);
                    const modalEl = deepSearch(document.body, "forceModalActionContainer");
                    if (modalEl) {
                        const buttons = modalEl.querySelectorAll("button");
                        if (buttons.length >= 2) reallySimulateClick(buttons[1]);
                    }
                }
            }

            await delay(500);
            removeStealthMode("Status zmieniony pomyślnie");

        } catch (e) {
            console.error(e);
            removeStealthMode("Błąd zamykania", true);
        }
    }


    async function runFillFields() {
        const root = getActiveTabRoot();
        if (!root) return;

        setStealthMode("Wypełnianie formularzy w tle...");

        try {
            // ORYGINAŁ: kliknięcie Edit Standard Solutions
            const solutionsBtn = findElementContainingTextDeep("Edit Standard Solutions", root);
            if (!clickElementSafe(solutionsBtn)) throw new Error("Brak Edit Standard Solutions");

            await delay(400);

            findElementInShadowDOM({
                root,
                ariaLabel: "Standard Solutions",
                spanToClick: "Other"
            });
            clickButtonByNameDeep("SaveEdit", root);

            // ORYGINAŁ: czekanie 2000ms (lekko skrócone do 1500) po kliknięciu Save
            await delay(1500);

            // ORYGINAŁ: kliknięcie Edit Sub Reason
            const editBtn = findElementContainingTextDeep("Edit Sub Reason", root);
            if (!clickElementSafe(editBtn)) throw new Error("Brak Edit Sub Reason");

            await delay(1800);

            // ORYGINAŁ: kliknięcie Sub Reason
            const subReasonBtn = findElementContainingTextDeep("Sub Reason", root);
            if (subReasonBtn) clickElementSafe(subReasonBtn);

            await delay(600);

            // ORYGINAŁ: kliknięcie System Support i FleetVision
            const systemSupportSpan = findSpanByTitleDeep("System Support", root);
            if (systemSupportSpan) clickElementSafe(systemSupportSpan);

            findElementInShadowDOM({
                root,
                ariaLabel: "Service Platform",
                spanToClick: "FleetVision"
            });

            await delay(250);

            clickButtonByNameDeep("SaveEdit", root);

            await delay(600);
            removeStealthMode("Wypełnianie zakończone!");

        } catch (e) {
            console.error(e);
            removeStealthMode("Błąd wykonania", true);
        }
    }

    // =========================================================================
    // 4. INICJALIZACJA PRZYCISKÓW W UTILITY BAR
    // =========================================================================
    function createButton(text, bgColor, id) {
        const button = document.createElement('li');
        button.id = id;
        button.style.display = 'flex';
        button.style.alignItems = 'center';
        button.style.justifyContent = 'center';
        button.style.cursor = 'pointer';
        button.style.padding = '8px';
        button.style.margin = '5px';
        button.style.backgroundColor = bgColor;
        button.style.color = '#fff';
        button.style.borderRadius = '4px';
        button.style.fontWeight = 'bold';
        button.textContent = text;
        return button;
    }

    function dodajPrzyciskDoUtilityBar() {
        const utilityBar = document.querySelector('.utilitybar');
        if (!utilityBar) return;
        if (document.querySelector('#fill-fields-button')) return;

        const button = createButton('Wypełnij pola', '#0070d2', 'fill-fields-button');
        const buttonCloseValidation = createButton('Customer Validation','#3ba755', 'close-validation-button');
        const buttonClosed = createButton('Closed','#3ba755', 'close-closed-button');
        const buttonDuplicate = createButton('Duplicate','#3ba755', 'close-duplicate-button');

        const tabItems = document.querySelectorAll('.navexConsoleTabItem');
        tabItems.forEach(item => {
            item.addEventListener('click', () => {
                getActiveTabRoot();
                if (window.location.href.includes("lightning/r/Case/500S")) {
                    setTimeout(dodajPrzyciskDoUtilityBar, 3000);
                }
            });
        });

        buttonCloseValidation.addEventListener('click', () => closeCase('Customer Validation'));
        buttonClosed.addEventListener('click', () => closeCase('Closed'));
        buttonDuplicate.addEventListener('click', () => closeCase('Duplicate'));

        button.addEventListener('click', runFillFields);

        utilityBar.appendChild(button);
        utilityBar.appendChild(buttonCloseValidation);
        utilityBar.appendChild(buttonClosed);
        utilityBar.appendChild(buttonDuplicate);
    }

    setTimeout(dodajPrzyciskDoUtilityBar, 5000);
})();