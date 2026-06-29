// reklama — Vanilla JS Frontend Logic (Real-time updates & controls)

document.addEventListener("DOMContentLoaded", () => {
    // --- Global Elements and State ---
    let userScrolledUp = false;

    // --- Tab Selection ---
    const tabButtons = document.querySelectorAll(".tab-btn");
    const tabPanes = document.querySelectorAll(".tab-pane");

    tabButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            tabButtons.forEach(b => b.classList.remove("active"));
            tabPanes.forEach(p => p.classList.remove("active"));

            btn.classList.add("active");
            const activeTabId = btn.getAttribute("data-tab");
            document.getElementById(activeTabId).classList.add("active");
        });
    });

    // --- Console Scroll Lock ---
    const consoleBody = document.getElementById("system-console");
    consoleBody.addEventListener("scroll", () => {
        // If the scroll is near the bottom, lock scrolling. Otherwise, let user inspect logs.
        const threshold = 15;
        userScrolledUp = (consoleBody.scrollHeight - consoleBody.scrollTop - consoleBody.clientHeight) > threshold;
    });

    // --- Format Timers ---
    function formatTime(seconds) {
        if (!seconds || seconds <= 0) return "-";
        const secs = Math.ceil(seconds);
        if (secs < 60) return `${secs} сек`;
        const mins = Math.floor(secs / 60);
        const rem = secs % 60;
        return `${mins} мин ${rem} сек`;
    }

    // --- API Helper Methods ---
    async function apiRequest(url, method = "GET", body = null) {
        const options = {
            method,
            headers: {}
        };
        if (body) {
            if (body instanceof FormData) {
                options.body = body;
            } else {
                options.headers["Content-Type"] = "application/json";
                options.body = JSON.stringify(body);
            }
        }
        try {
            const res = await fetch(url, options);
            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || `Ошибка сервера: ${res.status}`);
            }
            return await res.json();
        } catch (e) {
            appendConsoleLine("SYSTEM", `Критическая ошибка API: ${e.message}`, "CRITICAL");
            throw e;
        }
    }

    // --- Console Log Appending ---
    function appendConsoleLine(name, message, level = "INFO") {
        const line = document.createElement("div");
        line.className = "console-line";
        
        const timeSpan = document.createElement("span");
        timeSpan.className = "console-time";
        timeSpan.textContent = new Date().toLocaleTimeString();
        
        const textSpan = document.createElement("span");
        let lvlClass = `level-${level.toLowerCase()}`;
        textSpan.className = `console-text ${lvlClass}`;
        
        if (message.includes("ОТПРАВЛЕНО")) {
            textSpan.classList.add("success-highlight");
        }
        
        textSpan.textContent = `[${name}] ${message}`;
        
        line.appendChild(timeSpan);
        line.appendChild(textSpan);
        consoleBody.appendChild(line);

        if (!userScrolledUp) {
            consoleBody.scrollTop = consoleBody.scrollHeight;
        }
    }

    // --- Polling Status & Statistics ---
    async function pollStatus() {
        try {
            const data = await apiRequest("/api/status");
            
            // 0. Update account selector & list
            updateAccountsUI(data.accounts || [], data.active_account);
            
            // 1. Update Auth Status Wizard (active account)
            updateAuthUI(data.auth);
            
            // 2. Update Campaign State & Stats
            updateCampaignUI(data.campaign);
            
            // 3. Update Group Search State
            updateSearchUI(data.search);

            // 4. Update Rotator State
            if (data.rotator) updateRotatorUI(data.rotator);
            
        } catch (e) {
            console.error("Error polling status:", e);
        }
    }

    let currentActiveAccount = null;

    function updateAccountsUI(accounts, activeName) {
        const select = document.getElementById("select-account");
        const selectorContainer = document.getElementById("account-selector-container");

        if (!accounts.length) {
            selectorContainer.classList.add("hidden");
            document.getElementById("accounts-list").innerHTML = "<p class='panel-desc italic'>Аккаунты не настроены.</p>";
            return;
        }
        selectorContainer.classList.remove("hidden");

        // Перестраиваем select только если изменился состав
        const names = accounts.map(a => a.name).join(",");
        if (select.dataset.names !== names) {
            select.innerHTML = "";
            accounts.forEach(a => {
                const opt = document.createElement("option");
                opt.value = a.name;
                const statusMark = a.authorized ? "✓" : "✗";
                opt.textContent = `${statusMark} ${a.name}`;
                select.appendChild(opt);
            });
            select.dataset.names = names;
        }
        if (activeName && activeName !== currentActiveAccount) {
            select.value = activeName;
            currentActiveAccount = activeName;
        }

        // Список аккаунтов в настройках
        const listEl = document.getElementById("accounts-list");
        listEl.innerHTML = "";
        accounts.forEach(a => {
            const row = document.createElement("div");
            row.className = "account-row";
            const dotClass = a.authorized ? "online" : "offline";
            const info = a.account_info;
            const who = info ? `${info.first_name} (@${info.username || ''})` : "—";
            const isActive = a.name === activeName;
            row.innerHTML = `
                <span class="status-dot ${dotClass}"></span>
                <span class="account-name"><strong>${a.name}</strong>${isActive ? ' <span class="acc-active-badge">активен</span>' : ''}</span>
                <span class="account-who">${who}</span>
                <button class="btn ${isActive ? 'info' : 'primary'} btn-sm use-account-btn" data-name="${a.name}" ${isActive ? 'disabled' : ''}>Сделать активным</button>
            `;
            listEl.appendChild(row);
        });
        listEl.querySelectorAll(".use-account-btn").forEach(btn => {
            btn.addEventListener("click", async () => {
                try {
                    await apiRequest("/api/accounts/active", "POST", { name: btn.dataset.name });
                    appendConsoleLine("SYSTEM", `Активный аккаунт: ${btn.dataset.name}`, "INFO");
                    pollStatus();
                } catch (e) { console.error(e); }
            });
        });
    }

    function updateAuthUI(auth) {
        const authStatusContainer = document.getElementById("auth-status-container");
        const authWizardSection = document.getElementById("auth-wizard-section");
        const authStepPhone = document.getElementById("auth-step-phone");
        const authStepCode = document.getElementById("auth-step-code");
        const auth2faContainer = document.getElementById("auth-2fa-container");

        if (auth.status === "authorized") {
            // Connected user header indicator
            authStatusContainer.innerHTML = `
                <span class="status-dot online"></span>
                <span class="status-text">${auth.account.first_name} (@${auth.account.username || ''})</span>
                <button id="btn-logout" class="btn logout-btn ml-2">Выйти</button>
            `;
            // Add click listener to the newly generated logout button
            document.getElementById("btn-logout").onclick = handleLogout;
            
            authWizardSection.classList.add("hidden");
        } else if (auth.status === "code_sent") {
            authStatusContainer.innerHTML = `
                <span class="status-dot pending"></span>
                <span class="status-text">Ожидает код подтверждения</span>
            `;
            authWizardSection.classList.remove("hidden");
            authStepPhone.classList.add("hidden");
            authStepCode.classList.remove("hidden");
        } else {
            // Unauthorized
            authStatusContainer.innerHTML = `
                <span class="status-dot offline"></span>
                <span class="status-text">Не авторизован</span>
            `;
            authWizardSection.classList.remove("hidden");
            authStepPhone.classList.remove("hidden");
            authStepCode.classList.add("hidden");
            auth2faContainer.classList.add("hidden");
        }
    }

    function updateCampaignUI(campaign) {
        // Buttons reference
        const btnStart = document.getElementById("btn-campaign-start");
        const btnPause = document.getElementById("btn-campaign-pause");
        const btnResume = document.getElementById("btn-campaign-resume");
        const btnSkip = document.getElementById("btn-campaign-skip");
        const btnStop = document.getElementById("btn-campaign-stop");

        // Values reference
        const valSent = document.getElementById("stat-sent");
        const valSkipped = document.getElementById("stat-skipped");
        const valErrors = document.getElementById("stat-errors");
        const valTotal = document.getElementById("stat-total");
        
        const textProgressPct = document.getElementById("stat-progress-pct");
        const barFill = document.getElementById("stat-progress-bar");
        
        const textCurrentGroup = document.getElementById("stat-current-group");
        const textState = document.getElementById("stat-state");
        const textTimer = document.getElementById("stat-timer");
        const textMultiplier = document.getElementById("stat-multiplier");

        // Set values
        valSent.textContent = campaign.stats.sent;
        valSkipped.textContent = campaign.stats.skipped;
        valErrors.textContent = campaign.stats.errors;
        valTotal.textContent = campaign.stats.total;

        // Calculate progress percentage
        const done = campaign.stats.sent + campaign.stats.skipped + campaign.stats.errors;
        const total = Math.max(1, campaign.stats.total);
        const pct = Math.round((done / total) * 100);
        
        textProgressPct.textContent = `${pct}% (${done}/${campaign.stats.total})`;
        barFill.style.width = `${pct}%`;

        textCurrentGroup.textContent = campaign.stats.current_group || "Нет";
        textState.textContent = campaign.stats.state || "Ожидание";
        textMultiplier.textContent = `${campaign.stats.delay_multiplier.toFixed(2)}x`;

        // Delay timer formatting
        if (campaign.stats.timer_remaining > 0) {
            textTimer.textContent = formatTime(campaign.stats.timer_remaining);
            btnSkip.classList.remove("hidden");
        } else {
            textTimer.textContent = "-";
            btnSkip.classList.add("hidden");
        }

        // Show/hide execution control buttons based on running status
        if (campaign.running) {
            btnStart.classList.add("hidden");
            btnStop.classList.remove("hidden");

            if (campaign.status === "paused") {
                btnPause.classList.add("hidden");
                btnResume.classList.remove("hidden");
            } else {
                btnPause.classList.remove("hidden");
                btnResume.classList.add("hidden");
            }
        } else {
            btnStart.classList.remove("hidden");
            btnStop.classList.add("hidden");
            btnPause.classList.add("hidden");
            btnResume.classList.add("hidden");
            btnSkip.classList.add("hidden");
        }
    }

    function updateSearchUI(search) {
        const btnStartSearch = document.getElementById("btn-search-start");
        const btnStopSearch = document.getElementById("btn-search-stop");
        const monitorCard = document.getElementById("search-monitor");
        
        const monitorStatus = document.getElementById("search-stat-status");
        const monitorGroup = document.getElementById("search-stat-group");
        const monitorJoined = document.getElementById("search-stat-joined");
        const monitorTimer = document.getElementById("search-stat-timer");

        if (search.running) {
            btnStartSearch.classList.add("hidden");
            btnStopSearch.classList.remove("hidden");
            monitorCard.classList.remove("hidden");
            
            monitorStatus.textContent = search.stats.status || "Обработка...";
            monitorGroup.textContent = search.stats.current_group || "Нет";
            monitorJoined.textContent = `${search.stats.joined_count} из ${search.stats.total_found}`;
            
            if (search.stats.timer_remaining > 0) {
                monitorTimer.textContent = formatTime(search.stats.timer_remaining);
            } else {
                monitorTimer.textContent = "-";
            }
        } else {
            btnStartSearch.classList.remove("hidden");
            btnStopSearch.classList.add("hidden");
            if (search.stats.status === "Завершено") {
                monitorCard.classList.remove("hidden");
                monitorStatus.textContent = "Поиск завершен";
                monitorGroup.textContent = "Нет";
                monitorJoined.textContent = `${search.stats.joined_count} из ${search.stats.total_found}`;
                monitorTimer.textContent = "-";
            } else {
                monitorCard.classList.add("hidden");
            }
        }
    }

    // --- Polling System Logs ---
    let lastLogIndex = 0;
    async function pollLogs() {
        try {
            const logs = await apiRequest("/api/logs");
            
            // Check if we have new logs
            if (logs.length > lastLogIndex) {
                // If it is the first load, show all. Otherwise, append only new logs
                const newRecords = lastLogIndex === 0 ? logs : logs.slice(lastLogIndex);
                
                newRecords.forEach(log => {
                    const line = document.createElement("div");
                    line.className = "console-line";
                    
                    const timeSpan = document.createElement("span");
                    timeSpan.className = "console-time";
                    timeSpan.textContent = log.timestamp.split(" ")[1];
                    
                    const textSpan = document.createElement("span");
                    let lvlClass = `level-${log.level.toLowerCase()}`;
                    textSpan.className = `console-text ${lvlClass}`;
                    
                    if (log.message.includes("ОТПРАВЛЕНО")) {
                        textSpan.classList.add("success-highlight");
                    }
                    
                    textSpan.textContent = `[${log.name}] ${log.message}`;
                    
                    line.appendChild(timeSpan);
                    line.appendChild(textSpan);
                    consoleBody.appendChild(line);
                });
                
                lastLogIndex = logs.length;

                // Auto Scroll to bottom
                if (!userScrolledUp) {
                    consoleBody.scrollTop = consoleBody.scrollHeight;
                }
            }
        } catch (e) {
            console.error("Error polling logs:", e);
        }
    }

    // --- Event Handlers: Auth flow ---
    const inputPhone = document.getElementById("input-phone");
    const inputCode = document.getElementById("input-code");
    const input2fa = document.getElementById("input-2fa");
    const btnSendCode = document.getElementById("btn-send-code");
    const btnSubmitCode = document.getElementById("btn-submit-code");
    const auth2faContainer = document.getElementById("auth-2fa-container");

    btnSendCode.addEventListener("click", async () => {
        const phone = inputPhone.value.trim();
        if (!phone) return alert("Введите номер телефона.");
        
        btnSendCode.disabled = true;
        btnSendCode.textContent = "Отправка...";
        try {
            const res = await apiRequest("/api/auth/send-code", "POST", { phone });
            appendConsoleLine("SYSTEM", res.message, "WARNING");
            pollStatus();
        } catch (e) {
            btnSendCode.disabled = false;
            btnSendCode.textContent = "Получить код";
        }
    });

    btnSubmitCode.addEventListener("click", async () => {
        const code = inputCode.value.trim();
        const password = input2fa.value.trim();
        if (!code) return alert("Введите код подтверждения.");

        btnSubmitCode.disabled = true;
        btnSubmitCode.textContent = "Вход...";
        try {
            const res = await apiRequest("/api/auth/submit-code", "POST", { code, password: password || null });
            if (res.requires_password) {
                auth2faContainer.classList.remove("hidden");
                appendConsoleLine("SYSTEM", res.message, "WARNING");
                btnSubmitCode.disabled = false;
                btnSubmitCode.textContent = "Войти";
            } else {
                appendConsoleLine("SYSTEM", `Вход успешно выполнен как @${res.username}`, "INFO");
                inputCode.value = "";
                input2fa.value = "";
                pollStatus();
            }
        } catch (e) {
            btnSubmitCode.disabled = false;
            btnSubmitCode.textContent = "Войти";
        }
    });

    async function handleLogout() {
        if (!confirm("Вы действительно хотите выйти из аккаунта? Это удалит сессию.")) return;
        try {
            await apiRequest("/api/auth/logout", "POST");
            appendConsoleLine("SYSTEM", "Вы успешно вышли из аккаунта. Сессия стерта.", "WARNING");
            pollStatus();
        } catch (e) {
            console.error(e);
        }
    }

    // --- Event Handlers: Campaign Actions ---
    const btnCampStart = document.getElementById("btn-campaign-start");
    const btnCampPause = document.getElementById("btn-campaign-pause");
    const btnCampResume = document.getElementById("btn-campaign-resume");
    const btnCampSkip = document.getElementById("btn-campaign-skip");
    const btnCampStop = document.getElementById("btn-campaign-stop");
    
    const inputLimit = document.getElementById("limit-groups");
    const checkDryRun = document.getElementById("check-dry-run");
    const checkResetProgress = document.getElementById("check-reset-progress");

    btnCampStart.addEventListener("click", async () => {
        const limitVal = parseInt(inputLimit.value);
        const limit = limitVal > 0 ? limitVal : null;
        const dry_run = checkDryRun.checked;
        const reset_progress = checkResetProgress.checked;

        try {
            await apiRequest("/api/campaign/start", "POST", { dry_run, limit, reset_progress });
            pollStatus();
        } catch (e) {
            console.error(e);
        }
    });

    btnCampStop.addEventListener("click", async () => {
        try {
            await apiRequest("/api/campaign/stop", "POST");
            pollStatus();
        } catch (e) {
            console.error(e);
        }
    });

    btnCampPause.addEventListener("click", async () => {
        try {
            await apiRequest("/api/campaign/pause", "POST");
            pollStatus();
        } catch (e) {
            console.error(e);
        }
    });

    btnCampResume.addEventListener("click", async () => {
        try {
            await apiRequest("/api/campaign/resume", "POST");
            pollStatus();
        } catch (e) {
            console.error(e);
        }
    });

    btnCampSkip.addEventListener("click", async () => {
        try {
            await apiRequest("/api/campaign/skip-delay", "POST");
            pollStatus();
        } catch (e) {
            console.error(e);
        }
    });

    // --- Rotator UI Update ---
    function updateRotatorUI(rotator) {
        const btnStart = document.getElementById("btn-rotator-start");
        const btnStop = document.getElementById("btn-rotator-stop");
        const card = document.getElementById("rotator-status-card");
        const statStatus = document.getElementById("rotator-stat-status");
        const statAccount = document.getElementById("rotator-stat-account");
        const statCycle = document.getElementById("rotator-stat-cycle");
        const statCyclesByAccount = document.getElementById("rotator-stat-cycles-by-account");

        if (!btnStart) return; // elements not yet in DOM

        if (rotator.running) {
            btnStart.classList.add("hidden");
            btnStop.classList.remove("hidden");
            card.classList.remove("hidden");

            const statusMap = {
                "running": "Рассылка идёт...",
                "switching": "Пауза перед сменой аккаунта...",
                "stopping": "Завершаем текущий цикл...",
            };
            statStatus.textContent = statusMap[rotator.stats.status] || rotator.stats.status || "Работает";
            statAccount.textContent = rotator.stats.current_account || "—";
            statCycle.textContent = `#${rotator.stats.cycle_number || 0}`;

            // Build per-account cycles string
            const byAcc = rotator.stats.cycles_by_account || {};
            const parts = Object.entries(byAcc).map(([name, count]) => `${name}: ${count}`);
            statCyclesByAccount.textContent = parts.length ? parts.join(" | ") : "—";
        } else {
            btnStart.classList.remove("hidden");
            btnStop.classList.add("hidden");

            if (rotator.stats.status === "stopped" && rotator.stats.cycle_number > 0) {
                // Show final state after stop
                card.classList.remove("hidden");
                statStatus.textContent = "Ротация остановлена";
            } else {
                card.classList.add("hidden");
            }
        }
    }

    // --- Event Handlers: Rotator ---
    const btnRotatorStart = document.getElementById("btn-rotator-start");
    const btnRotatorStop = document.getElementById("btn-rotator-stop");
    const inputRotatorPause = document.getElementById("rotator-pause");
    const checkRotatorReset = document.getElementById("rotator-reset-each");

    btnRotatorStart.addEventListener("click", async () => {
        const pause_between_sec = parseInt(inputRotatorPause.value) || 60;
        const reset_each_cycle = checkRotatorReset.checked;
        try {
            const res = await apiRequest("/api/rotator/start", "POST", { pause_between_sec, reset_each_cycle });
            appendConsoleLine("ROTATOR", `Ротация запущена: ${(res.accounts || []).join(" → ")}`, "INFO");
            pollStatus();
        } catch (e) {
            console.error(e);
        }
    });

    btnRotatorStop.addEventListener("click", async () => {
        try {
            const res = await apiRequest("/api/rotator/stop", "POST");
            appendConsoleLine("ROTATOR", res.message || "Сигнал остановки отправлен.", "WARNING");
            pollStatus();
        } catch (e) {
            console.error(e);
        }
    });

    // --- Event Handlers: Search and Auto-Join Actions ---
    const btnSearchStart = document.getElementById("btn-search-start");
    const btnSearchStop = document.getElementById("btn-search-stop");
    const inputQuery = document.getElementById("search-query");
    const inputLinks = document.getElementById("search-links");
    const checkJoin = document.getElementById("search-check-join");
    const inputSearchLimit = document.getElementById("search-limit");

    btnSearchStart.addEventListener("click", async () => {
        const query = inputQuery.value.trim() || null;
        const linksText = inputLinks.value.trim();
        const links = linksText ? linksText.split("\n").map(l => l.trim()).filter(l => l) : null;
        const join = checkJoin.checked;
        const limit = parseInt(inputSearchLimit.value) || 20;

        if (!query && !links) {
            return alert("Укажите хотя бы одно ключевое слово или список ссылок для поиска.");
        }

        try {
            await apiRequest("/api/search/start", "POST", { query, links, join, limit });
            pollStatus();
        } catch (e) {
            console.error(e);
        }
    });

    btnSearchStop.addEventListener("click", async () => {
        try {
            await apiRequest("/api/search/stop", "POST");
            pollStatus();
        } catch (e) {
            console.error(e);
        }
    });

    // --- Event Handlers: Message & Media ---
    const textMsg = document.getElementById("message-text");
    const labelMedia = document.getElementById("media-filename");
    const inputMediaUpload = document.getElementById("media-upload-input");
    const btnMsgSave = document.getElementById("btn-message-save");
    const btnMediaDelete = document.getElementById("btn-media-delete");

    async function loadMessageData() {
        try {
            const data = await apiRequest("/api/message");
            textMsg.value = data.text;
            if (data.media) {
                labelMedia.textContent = data.media;
                btnMediaDelete.classList.remove("hidden");
            } else {
                labelMedia.textContent = "Медиа отсутствует";
                btnMediaDelete.classList.add("hidden");
            }
        } catch (e) {
            console.error(e);
        }
    }

    btnMsgSave.addEventListener("click", async () => {
        try {
            await apiRequest("/api/message", "POST", { text: textMsg.value });
            alert("Текст сообщения успешно сохранен.");
        } catch (e) {
            console.error(e);
        }
    });

    inputMediaUpload.addEventListener("change", async () => {
        const file = inputMediaUpload.files[0];
        if (!file) return;

        const formData = new FormData();
        formData.append("file", file);

        try {
            await apiRequest("/api/message/media", "POST", formData);
            loadMessageData();
            inputMediaUpload.value = ""; // Reset
        } catch (e) {
            console.error(e);
        }
    });

    btnMediaDelete.addEventListener("click", async () => {
        if (!confirm("Вы действительно хотите удалить медиафайл рассылки?")) return;
        try {
            await apiRequest("/api/message/media", "DELETE");
            loadMessageData();
        } catch (e) {
            console.error(e);
        }
    });

    // --- Event Handlers: Configuration Settings ---
    const inputCfgApiId = document.getElementById("cfg-api-id");
    const inputCfgApiHash = document.getElementById("cfg-api-hash");
    const inputCfgDelayMin = document.getElementById("cfg-delay-min");
    const inputCfgDelayMax = document.getElementById("cfg-delay-max");
    const inputCfgBatchSize = document.getElementById("cfg-batch-size");
    const inputCfgBatchPauseMin = document.getElementById("cfg-batch-pause-min");
    const inputCfgBatchPauseMax = document.getElementById("cfg-batch-pause-max");
    const inputCfgActiveHours = document.getElementById("cfg-active-hours");
    const btnCfgSave = document.getElementById("btn-config-save");

    async function loadConfigData() {
        try {
            const data = await apiRequest("/api/config");
            inputCfgApiId.value = data.TELEGRAM_API_ID || "";
            inputCfgApiHash.value = data.TELEGRAM_API_HASH || "";
            inputCfgDelayMin.value = data.DELAY_MIN_SEC || "30";
            inputCfgDelayMax.value = data.DELAY_MAX_SEC || "90";
            inputCfgBatchSize.value = data.BATCH_SIZE || "50";
            inputCfgBatchPauseMin.value = data.BATCH_PAUSE_MIN_SEC || "300";
            inputCfgBatchPauseMax.value = data.BATCH_PAUSE_MAX_SEC || "900";
            inputCfgActiveHours.value = data.ACTIVE_HOURS || "";
        } catch (e) {
            console.error(e);
        }
    }

    btnCfgSave.addEventListener("click", async () => {
        const settings = {
            TELEGRAM_API_ID: inputCfgApiId.value.trim(),
            TELEGRAM_API_HASH: inputCfgApiHash.value.trim(),
            DELAY_MIN_SEC: inputCfgDelayMin.value.trim(),
            DELAY_MAX_SEC: inputCfgDelayMax.value.trim(),
            BATCH_SIZE: inputCfgBatchSize.value.trim(),
            BATCH_PAUSE_MIN_SEC: inputCfgBatchPauseMin.value.trim(),
            BATCH_PAUSE_MAX_SEC: inputCfgBatchPauseMax.value.trim(),
            ACTIVE_HOURS: inputCfgActiveHours.value.trim()
        };
        try {
            await apiRequest("/api/config", "POST", { settings });
            alert("Настройки конфигурации обновлены.");
        } catch (e) {
            console.error(e);
        }
    });

    // --- Init & Start Polling ---
    // Account selector switch
    const selectAccount = document.getElementById("select-account");
    selectAccount.addEventListener("change", async () => {
        try {
            await apiRequest("/api/accounts/active", "POST", { name: selectAccount.value });
            appendConsoleLine("SYSTEM", `Активный аккаунт: ${selectAccount.value}`, "INFO");
            // Сбрасываем визард авторизации при переключении
            document.getElementById("auth-step-phone").classList.remove("hidden");
            document.getElementById("auth-step-code").classList.add("hidden");
            document.getElementById("auth-2fa-container").classList.add("hidden");
            pollStatus();
        } catch (e) { console.error(e); }
    });

    // Add account
    const btnAccountAdd = document.getElementById("btn-account-add");
    btnAccountAdd.addEventListener("click", async () => {
        const name = document.getElementById("new-acc-name").value.trim();
        const api_id_raw = document.getElementById("new-acc-api-id").value.trim();
        const api_hash = document.getElementById("new-acc-api-hash").value.trim();
        if (!api_id_raw || !api_hash) return alert("Укажите API ID и API HASH нового аккаунта.");
        const api_id = parseInt(api_id_raw, 10);
        if (!api_id) return alert("API ID должен быть числом.");
        try {
            const res = await apiRequest("/api/accounts/add", "POST", { api_id, api_hash, name });
            appendConsoleLine("SYSTEM", `Аккаунт «${res.name}» добавлен.`, "INFO");
            document.getElementById("new-acc-name").value = "";
            document.getElementById("new-acc-api-id").value = "";
            document.getElementById("new-acc-api-hash").value = "";
            pollStatus();
        } catch (e) { console.error(e); }
    });

    loadMessageData();
    loadConfigData();
    pollStatus();
    pollLogs();
    
    setInterval(pollStatus, 1000);
    setInterval(pollLogs, 1000);
});
