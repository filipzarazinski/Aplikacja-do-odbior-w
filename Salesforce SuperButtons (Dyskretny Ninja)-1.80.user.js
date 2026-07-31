// ==UserScript==
// @name         Salesforce SuperButtons (Dyskretny Ninja)
// @namespace    http://tampermonkey.net/
// @version      1.80
// @description  Stabilna wersja z waitFor zamiast stałych opóźnień.
// @match        *://*.lightning.force.com/*
// @grant        none
// ==/UserScript==

(function () {
    'use strict';

    const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

    // =========================================================================
    // 1. GHOST MODE & TOAST
    // =========================================================================
    const style = document.createElement('style');
    style.innerHTML = `
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
        setTimeout(() => document.body.classList.remove('sf-stealth-mode'), 200);
        if (msg) showToast(msg, isError ? 'error' : 'success');
        else toastEl.className = '';
    }

    // =========================================================================
    // 2. NARZĘDZIE: waitFor — polling zamiast stałych opóźnień
    // =========================================================================
    async function waitFor(predicate, { timeout = 6000, interval = 120, label = 'element' } = {}) {
        const start = Date.now();
        while (Date.now() - start < timeout) {
            const result = predicate();
            if (result) return result;
            await delay(interval);
        }
        throw new Error(`Timeout (${timeout}ms): nie znaleziono "${label}"`);
    }

    // =========================================================================
    // 3. FUNKCJE DOM (przeszukiwanie przez Shadow DOM)
    // =========================================================================
    function getActiveTabRoot() {
        const rootContainer = document.querySelector('div.split-right[role="main"]');
        if (!rootContainer) return null;
        return Array.from(rootContainer.children).find(child =>
            child.matches?.('section.tabContent.active.oneConsoleTab[role="tabpanel"]')
        ) || rootContainer;
    }

    function deepFind(root, predicate, { useShadow = true } = {}) {
        const seen = new Set();
        function dfs(el) {
            if (!el || seen.has(el)) return null;
            seen.add(el);
            if (predicate(el)) return el;
            for (const child of el.children) {
                const r = dfs(child);
                if (r) return r;
            }
            if (useShadow && el.shadowRoot) {
                for (const child of el.shadowRoot.children) {
                    const r = dfs(child);
                    if (r) return r;
                }
            }
            return null;
        }
        return dfs(root);
    }

    // Szuka przycisku inline-edit dla pola SF.
    // Inline-edit triggery SF są ukryte (pojawiają się na hover) ale są w DOM.
    // Trzy strategie: po title, po span assistive-text, po klasie inline-edit-trigger.
    function findInlineEditTrigger(fieldTitle) {
        const titleToFind = `Edit ${fieldTitle}`;

        // Strategia 1: szukaj button z dokładnym title
        const byTitle = deepFind(document.body, el =>
            el.tagName === 'BUTTON' && el.getAttribute?.('title') === titleToFind
        );
        if (byTitle) {
            console.log(`[SF Ninja] trigger po title: "${titleToFind}"`);
            return byTitle;
        }

        // Strategia 2: span.slds-assistive-text z tekstem → idź w górę do button
        const bySpan = deepFind(document.body, el =>
            el.tagName === 'SPAN' &&
            el.classList?.contains('slds-assistive-text') &&
            el.textContent?.trim() === titleToFind
        );
        if (bySpan) {
            let p = bySpan.parentElement;
            while (p && p.tagName !== 'BUTTON') p = p.parentElement;
            if (p) {
                console.log(`[SF Ninja] trigger po slds-assistive-text: "${titleToFind}"`);
                return p;
            }
        }

        // Strategia 3: button.inline-edit-trigger z tytułem pasującym do fieldTitle
        const byClass = deepFind(document.body, el =>
            el.tagName === 'BUTTON' &&
            el.classList?.contains('inline-edit-trigger') &&
            el.getAttribute?.('title')?.includes(fieldTitle)
        );
        if (byClass) {
            console.log(`[SF Ninja] trigger po inline-edit-trigger dla: "${fieldTitle}"`);
            return byClass;
        }

        // Diagnostyka
        const allEdits = [];
        deepFind(document.body, el => {
            if (el.tagName === 'BUTTON' && el.getAttribute?.('title')?.startsWith('Edit')) {
                allEdits.push(el.getAttribute('title'));
            }
            return false;
        });
        console.log(`[SF Ninja] Brak triggera dla "${titleToFind}". Dostępne Edit-przyciski:`, allEdits);
        return null;
    }

    // Szuka opcji dropdownu po tekście (przeszukuje przez shadow DOM).
    function findDropdownOption(text) {
        function optionMatches(el) {
            if (el.getAttribute?.('title') === text) return true;
            if (el.getAttribute?.('data-value') === text) return true;
            if (el.textContent?.trim() === text) return true;
            try { if (el.innerText?.trim() === text) return true; } catch(e) {}
            if (el.shadowRoot) {
                if (el.shadowRoot.querySelector?.(`[title="${text}"]`)) return true;
                const all = el.shadowRoot.querySelectorAll?.('*') || [];
                for (const c of all) {
                    if (c.getAttribute?.('title') === text) return true;
                    if (c.textContent?.trim() === text) return true;
                }
            }
            return false;
        }

        function search(el, seen = new Set()) {
            if (!el || seen.has(el)) return null;
            seen.add(el);
            if (el.getAttribute?.('role') === 'option') {
                return optionMatches(el) ? el : null;
            }
            for (const child of el.children) {
                const r = search(child, seen);
                if (r) return r;
            }
            if (el.shadowRoot) {
                for (const child of el.shadowRoot.children) {
                    const r = search(child, seen);
                    if (r) return r;
                }
            }
            return null;
        }
        return search(document.body);
    }

    // Szuka triggera combobox po aria-label: role="combobox" lub klasa slds-combobox__input
    function findComboboxTrigger(label, root) {
        return deepFind(root, el =>
            el.tagName === 'BUTTON' &&
            el.getAttribute?.('aria-label') === label &&
            (el.getAttribute?.('role') === 'combobox' || el.className?.includes('slds-combobox__input'))
        );
    }

    // Szuka button z danym name
    function findButtonByName(name, root) {
        return deepFind(root, el =>
            el.tagName === 'BUTTON' && el.getAttribute?.('name') === name
        );
    }

    // Szuka przycisku Save — próbuje name="SaveEdit", name="Save", slds-button_brand
    function findSaveButton(root) {
        return (
            findButtonByName('SaveEdit', root) ||
            findButtonByName('Save', root) ||
            deepFind(root, el =>
                el.tagName === 'BUTTON' &&
                (el.classList?.contains('slds-button_brand') || el.classList?.contains('slds-button--brand')) &&
                !el.disabled
            )
        );
    }

    // Szuka SELECT z klasami
    function findSelectByClasses(classes, root) {
        return deepFind(root, el =>
            el.tagName === 'SELECT' && classes.every(c => el.classList.contains(c))
        );
    }

    // Symuluje hover + kliknięcie (potrzebne dla inline-edit triggerów ukrytych do hover)
    function hoverAndClick(el) {
        if (!el) return;
        // Hover — SF inline-edit triggery wymagają mouseover żeby stały się aktywne
        el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, cancelable: true }));
        el.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true, cancelable: true }));
        el.focus();
        el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }));
        el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
        el.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true }));
        el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
        el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        el.click(); // natywny click jako ostatni fallback
    }

    // Symuluje kliknięcie (bez hover — dla comboboxów i przycisków które są już aktywne)
    function reallySimulateClick(el) {
        if (!el) return;
        el.focus();
        el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }));
        el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
        el.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true }));
        el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
        el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        el.click();
    }

    // =========================================================================
    // 4. GŁÓWNA LOGIKA — runFillFields
    // =========================================================================
    async function runFillFields() {
        const root = getActiveTabRoot();
        if (!root) { showToast('Brak aktywnej karty SF', 'error'); return; }

        setStealthMode('Wypełnianie pól...');

        try {
            // --- Krok 1: kliknij przycisk ołówka "Edit Standard Solutions" ---
            // Trigger jest ukryty do hover — używamy hoverAndClick
            const editStdSol = await waitFor(
                () => findInlineEditTrigger('Standard Solutions'),
                { label: 'Edit Standard Solutions' }
            );
            console.log('[SF Ninja] Krok 1: klikam trigger:', editStdSol);
            hoverAndClick(editStdSol);

            // --- Krok 2: poczekaj na formularz (trigger combobox) i otwórz dropdown ---
            await delay(400); // formularz potrzebuje chwili na pojawienie się
            const stdSolField = await waitFor(
                () => findComboboxTrigger('Standard Solutions', document.body),
                { label: 'trigger Standard Solutions', timeout: 8000 }
            );
            console.log('[SF Ninja] Krok 2: klikam combobox:', stdSolField);
            reallySimulateClick(stdSolField);

            // --- Krok 3: poczekaj na opcję "Other" w dropdownie ---
            const otherOption = await waitFor(
                () => findDropdownOption('Other'),
                { label: 'opcja Other', timeout: 5000 }
            );
            console.log('[SF Ninja] Krok 3: klikam Other:', otherOption);
            reallySimulateClick(otherOption);

            // --- Krok 4: poczekaj na przycisk Save (name="SaveEdit") i kliknij ---
            await delay(200);
            const saveBtn1 = await waitFor(
                () => findSaveButton(document.body),
                { label: 'Save (krok 1)', timeout: 5000 }
            );
            console.log('[SF Ninja] Krok 4: klikam Save:', saveBtn1);
            await delay(100);
            saveBtn1.click();

            // --- Krok 5: poczekaj aż formularz Standard Solutions zniknie ---
            await waitFor(
                () => !findComboboxTrigger('Standard Solutions', document.body),
                { label: 'zakończenie zapisu krok 1', timeout: 8000 }
            );
            await delay(400);

            // --- Krok 6: kliknij przycisk "Edit Sub Reason" ---
            const editSubReason = await waitFor(
                () => findInlineEditTrigger('Sub Reason'),
                { label: 'Edit Sub Reason' }
            );
            console.log('[SF Ninja] Krok 6: klikam Edit Sub Reason:', editSubReason);
            hoverAndClick(editSubReason);

            // --- Krok 7: poczekaj na formularz Sub Reason i otwórz dropdown ---
            await delay(400);
            const subReasonField = await waitFor(
                () => findComboboxTrigger('Sub Reason', document.body),
                { label: 'trigger Sub Reason', timeout: 8000 }
            );
            console.log('[SF Ninja] Krok 7: klikam combobox Sub Reason:', subReasonField);
            reallySimulateClick(subReasonField);

            // --- Krok 8: poczekaj na opcję "System Support" ---
            const sysSupport = await waitFor(
                () => findDropdownOption('System Support'),
                { label: 'opcja System Support', timeout: 5000 }
            );
            console.log('[SF Ninja] Krok 8: klikam System Support:', sysSupport);
            reallySimulateClick(sysSupport);

            // --- Krok 9: poczekaj na combobox Service Platform i otwórz dropdown ---
            await delay(200);
            const servicePlatformField = await waitFor(
                () => findComboboxTrigger('Service Platform', document.body),
                { label: 'trigger Service Platform', timeout: 8000 }
            );
            console.log('[SF Ninja] Krok 9: klikam combobox Service Platform:', servicePlatformField);
            reallySimulateClick(servicePlatformField);

            // --- Krok 10: poczekaj na opcję "FleetVision" ---
            const fleetVision = await waitFor(
                () => findDropdownOption('FleetVision'),
                { label: 'opcja FleetVision', timeout: 5000 }
            );
            console.log('[SF Ninja] Krok 10: klikam FleetVision:', fleetVision);
            reallySimulateClick(fleetVision);

            // --- Krok 11: poczekaj na Save i zapisz ---
            await delay(200);
            const saveBtn2 = await waitFor(
                () => findSaveButton(document.body),
                { label: 'Save (krok 2)', timeout: 5000 }
            );
            console.log('[SF Ninja] Krok 11: klikam Save:', saveBtn2);
            await delay(100);
            saveBtn2.click();

            // Poczekaj aż formularz Sub Reason zniknie
            await waitFor(
                () => !findComboboxTrigger('Sub Reason', document.body),
                { label: 'zakończenie zapisu krok 2', timeout: 8000 }
            );

            removeStealthMode('Wypełnianie zakończone!');

        } catch (e) {
            console.error('[SF Ninja] runFillFields:', e);
            removeStealthMode(`Błąd: ${e.message}`, true);
        }
    }

    // =========================================================================
    // 5. GŁÓWNA LOGIKA — closeCase
    // =========================================================================
    async function closeCase(option) {
        const root = getActiveTabRoot();
        if (!root) { showToast('Brak aktywnej karty SF', 'error'); return; }

        setStealthMode(`Zamykanie: ${option}...`);

        try {
            const pathContainer = await waitFor(
                () => deepFind(root, el =>
                    el.classList?.contains('runtime_sales_pathassistantPathAssistantTabSet')
                ),
                { label: 'PathContainer', timeout: 5000 }
            );

            const pathNav = await waitFor(
                () => deepFind(pathContainer, el =>
                    el.tagName === 'UL' && el.classList.contains('slds-path__nav')
                ),
                { label: 'slds-path__nav' }
            );

            const liEls = pathNav.querySelectorAll('li');
            const lastLi = liEls[liEls.length - 1];
            const aTag = lastLi?.querySelector('a');
            if (aTag) aTag.click();

            await delay(300);

            const headerSibling = Array.from(pathContainer.parentElement?.children || []).find(
                sib => sib !== pathContainer &&
                       sib.classList.contains('runtime_sales_pathassistantPathAssistantHeader')
            );
            if (headerSibling) {
                const btn = headerSibling.querySelector('button');
                if (btn) btn.click();
            }

            const selectEl = await waitFor(
                () => findSelectByClasses(['stepAction', 'select'], document.body),
                { label: 'select StepClosed', timeout: 6000 }
            );

            selectEl.focus();
            selectEl.value = option;
            selectEl.dispatchEvent(new Event('input', { bubbles: true }));
            selectEl.dispatchEvent(new Event('change', { bubbles: true }));

            await delay(200);

            const confirmBtn = await waitFor(
                () => {
                    const modal = deepFind(document.body, el =>
                        el.classList?.contains('forceModalActionContainer') ||
                        el.classList?.contains('modal-footer')
                    );
                    if (!modal) return null;
                    const btns = modal.querySelectorAll('button');
                    return btns.length >= 2 ? btns[1] : btns[0] || null;
                },
                { label: 'przycisk potwierdzenia w modalu', timeout: 5000 }
            );
            reallySimulateClick(confirmBtn);

            await delay(500);
            removeStealthMode('Status zmieniony pomyślnie');

        } catch (e) {
            console.error('[SF Ninja] closeCase:', e);
            removeStealthMode(`Błąd: ${e.message}`, true);
        }
    }

    // =========================================================================
    // 6. PRZYCISKI W UTILITY BAR — trwałe dzięki MutationObserver
    // =========================================================================
    function createButton(text, bgColor, id) {
        const button = document.createElement('li');
        button.id = id;
        button.style.cssText = `
            display:flex; align-items:center; justify-content:center;
            cursor:pointer; padding:8px; margin:5px;
            background-color:${bgColor}; color:#fff;
            border-radius:4px; font-weight:bold; font-size:13px;
            user-select:none;
        `;
        button.textContent = text;
        return button;
    }

    function dodajPrzyciskDoUtilityBar() {
        const utilityBar = document.querySelector('.utilitybar');
        if (!utilityBar) return;
        if (document.querySelector('#fill-fields-button')) return;

        const button                = createButton('Wypełnij pola',       '#0070d2', 'fill-fields-button');
        const buttonCloseValidation = createButton('Customer Validation',  '#3ba755', 'close-validation-button');
        const buttonClosed          = createButton('Closed',               '#3ba755', 'close-closed-button');
        const buttonDuplicate       = createButton('Duplicate',            '#3ba755', 'close-duplicate-button');

        button.addEventListener('click', runFillFields);
        buttonCloseValidation.addEventListener('click', () => closeCase('Customer Validation'));
        buttonClosed.addEventListener('click',          () => closeCase('Closed'));
        buttonDuplicate.addEventListener('click',       () => closeCase('Duplicate'));

        utilityBar.appendChild(button);
        utilityBar.appendChild(buttonCloseValidation);
        utilityBar.appendChild(buttonClosed);
        utilityBar.appendChild(buttonDuplicate);
    }

    const barObserver = new MutationObserver(() => {
        const bar = document.querySelector('.utilitybar');
        if (bar && !document.querySelector('#fill-fields-button')) {
            dodajPrzyciskDoUtilityBar();
        }
    });
    barObserver.observe(document.body, { childList: true, subtree: true });

    setTimeout(dodajPrzyciskDoUtilityBar, 3000);

})();
