let products = [];
let productCategories = [];
let providerIndex = {};
let selectedProvider = null;
let selectedSku = null;
let selectedItemName = null;
let selectedPrice = null;
let selectedProviderData = null;
let currentOrderId = null;
let currentOrderAccessToken = localStorage.getItem("last_order_access_token") || "";
const ORDER_ACCESS_TOKENS_STORAGE_KEY = "order_access_tokens";
const MAX_STORED_ORDER_ACCESS_TOKENS = 10;
let checkoutIdempotencyKey = "";
let confirmModal;
let activePromos = [];
let appliedPromoCode = null;
let customerToken = localStorage.getItem("customer_token") || "";
let currentCustomer = null;
let activeCategoryFilter = "ALL";
let storefrontStats = {};
let paymentChannels = [];
let paymentChannelSource = "";
let paymentChannelLoading = false;
let paymentChannelLoadError = "";
let paymentChannelAbortController = null;
let paymentChannelRequestSequence = 0;
let currentPaymentQuote = null;
let paymentQuoteLoadingKey = "";
let paymentQuoteAbortController = null;
let paymentQuoteRequestSequence = 0;
let paymentQuoteRefreshTimer = null;
let paymentQuoteRefreshScheduled = false;
let paymentQuoteRequestPromise = null;
let paymentQuoteError = "";
let promoApplyInFlight = false;
let promoApplySequence = 0;
let promoRevalidationInFlight = false;
let promoRevalidationSequence = 0;
let lastConfirmedQuoteKey = "";
let selectedProduct = null;
let pendingPostpaidOrder = null;

// VARIABEL BARU: Kunci Anti-Kedip
let currentPopupStatus = null; 
let currentPopupPaymentKey = null;

const imageDb = {
    "mobile legends": "https://placehold.co/400x400/1e1e2f/ffffff?text=Mobile+Legends",
    "free fire": "https://placehold.co/400x400/ff5500/ffffff?text=Free+Fire",
    "pubg": "https://placehold.co/400x400/ffcc00/000000?text=PUBG",
    "axis": "https://placehold.co/400x400/660099/ffffff?text=AXIS",
    "default": "https://placehold.co/400x400/cccccc/000000?text=GAME"
};

function normalizeCheckoutSku(value) {
    return String(value ?? "").trim().toLowerCase();
}

function normalizePaymentCode(value) {
    return String(value ?? "").trim().toUpperCase();
}

function normalizeVoucherCode(value) {
    return String(value ?? "").trim().toUpperCase();
}

function isValidCheckoutPhone(value) {
    return String(value ?? "").replace(/\D/g, "").length >= 8;
}

function buildCheckoutQuotePayload(context = {}) {
    return {
        sku: normalizeCheckoutSku(context.sku),
        method: normalizePaymentCode(context.method),
        promo_code: normalizeVoucherCode(context.promoCode) || null,
        phone: String(context.phone ?? "").trim() || null,
        target_id: String(context.targetId ?? "").trim() || null,
    };
}

function buildCheckoutQuoteHeaders(token = "") {
    const headers = { "Content-Type": "application/json" };
    const normalizedToken = String(token || "").trim();
    if (normalizedToken) headers["customer-token"] = normalizedToken;
    return headers;
}

function storedOrderAccessTokens() {
    try {
        const parsed = JSON.parse(localStorage.getItem(ORDER_ACCESS_TOKENS_STORAGE_KEY) || "{}");
        return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch (_) {
        return {};
    }
}

function saveOrderAccessToken(orderId, token) {
    const tokens = storedOrderAccessTokens();
    if (token) {
        delete tokens[orderId];
        tokens[orderId] = token;
        const retainedEntries = Object.entries(tokens).slice(-MAX_STORED_ORDER_ACCESS_TOKENS);
        localStorage.setItem(ORDER_ACCESS_TOKENS_STORAGE_KEY, JSON.stringify(Object.fromEntries(retainedEntries)));
        return;
    }
    delete tokens[orderId];
    localStorage.setItem(ORDER_ACCESS_TOKENS_STORAGE_KEY, JSON.stringify(tokens));
}

function rememberOrderAccess(order) {
    const orderId = String(order?.id || "").trim();
    if (!orderId) return;
    currentOrderId = orderId;
    localStorage.setItem("last_order_id", currentOrderId);
    currentOrderAccessToken = String(order?.order_access_token || "").trim();
    saveOrderAccessToken(orderId, currentOrderAccessToken);
    if (currentOrderAccessToken) {
        localStorage.setItem("last_order_access_token", currentOrderAccessToken);
    } else {
        localStorage.removeItem("last_order_access_token");
    }
}

function clearRememberedOrder(orderId = currentOrderId) {
    const resolvedOrderId = String(orderId || "").trim();
    if (resolvedOrderId) saveOrderAccessToken(resolvedOrderId, "");

    const storedOrderId = localStorage.getItem("last_order_id") || "";
    if (!resolvedOrderId || storedOrderId === resolvedOrderId) {
        localStorage.removeItem("last_order_id");
        localStorage.removeItem("last_order_access_token");
    }
    if (!resolvedOrderId || String(currentOrderId || "") === resolvedOrderId) {
        currentOrderId = null;
        currentOrderAccessToken = "";
    }
}

function orderAccessHeaders(orderId = currentOrderId) {
    const headers = {};
    const resolvedOrderId = String(orderId || "");
    const storedOrderId = localStorage.getItem("last_order_id") || "";
    const storedToken = localStorage.getItem("last_order_access_token") || "";
    const tokenMap = storedOrderAccessTokens();
    if (currentOrderAccessToken && resolvedOrderId === String(currentOrderId || "")) {
        headers["X-Order-Access-Token"] = currentOrderAccessToken;
    } else if (tokenMap[resolvedOrderId]) {
        headers["X-Order-Access-Token"] = tokenMap[resolvedOrderId];
    } else if (storedToken && resolvedOrderId === storedOrderId) {
        headers["X-Order-Access-Token"] = storedToken;
    }
    if (customerToken) headers["customer-token"] = customerToken;
    return headers;
}

const PROMO_REASON_MESSAGES = {
    PROMO_NOT_FOUND: "Kode promo tidak ditemukan.",
    PROMO_INACTIVE: "Kode promo sedang tidak aktif.",
    PROMO_NOT_STARTED: "Periode promo belum dimulai.",
    PROMO_EXPIRED: "Periode promo sudah berakhir.",
    PRODUCT_NOT_ELIGIBLE: "Promo tidak berlaku untuk produk yang dipilih.",
    PAYMENT_METHOD_NOT_ELIGIBLE: "Promo tidak berlaku untuk metode pembayaran yang dipilih.",
    MINIMUM_TRANSACTION_NOT_MET: "Minimum transaksi untuk promo belum terpenuhi.",
    CUSTOMER_LIMIT_REACHED: "Batas penggunaan promo untuk pelanggan ini sudah tercapai.",
    PHONE_LIMIT_REACHED: "Batas penggunaan promo untuk nomor WhatsApp ini sudah tercapai.",
    TARGET_LIMIT_REACHED: "Batas penggunaan promo untuk target ini sudah tercapai.",
    QUOTA_EXHAUSTED: "Kuota promo sudah habis.",
    BUDGET_EXHAUSTED: "Anggaran promo sudah habis.",
    PROMO_CONFLICT: "Promo tidak dapat digabungkan dengan promo lain.",
    MEMBER_LOGIN_REQUIRED: "Masuk ke akun untuk menggunakan promo ini.",
    GUEST_ONLY_PROMO: "Promo ini hanya berlaku untuk checkout tanpa akun.",
    NEW_CUSTOMER_ONLY: "Promo ini hanya berlaku untuk pelanggan baru.",
    EXISTING_CUSTOMER_ONLY: "Promo ini hanya berlaku untuk pelanggan yang sudah pernah bertransaksi.",
    CUSTOMER_NOT_TARGETED: "Promo ini tidak ditujukan untuk akun atau nomor Anda.",
    CUSTOMER_IDENTITY_REQUIRED: "Masukkan identitas pelanggan untuk menggunakan promo ini.",
    ACCOUNT_INACTIVE: "Akun Anda tidak aktif dan tidak dapat menggunakan promo member.",
    ELIGIBILITY_RULE_NOT_MET: "Syarat pelanggan untuk promo ini belum terpenuhi.",
    ACCOUNT_TOO_OLD: "Promo ini hanya berlaku untuk akun yang dibuat dalam batas waktu yang ditentukan.",
    FIRST_PURCHASE_ONLY: "Promo ini hanya berlaku untuk pembeli pertama yang belum pernah bertransaksi berhasil.",
    MIN_SUCCESSFUL_ORDERS_NOT_MET: "Jumlah transaksi berhasil Anda belum memenuhi syarat promo.",
    MIN_SUCCESSFUL_SPEND_NOT_MET: "Total transaksi berhasil Anda belum memenuhi syarat promo.",
    INVALID_ELIGIBILITY_RULE: "Aturan promo sedang tidak valid. Silakan pilih promo lain.",
    PHONE_REQUIRED: "Masukkan nomor WhatsApp untuk menggunakan promo ini.",
    TARGET_ID_REQUIRED: "Masukkan ID tujuan untuk menggunakan promo ini.",
    QUOTE_NOT_READY: "Tunggu perhitungan harga selesai",
    INVALID_TRANSACTION_CONTEXT: "Lengkapi data transaksi sebelum menggunakan promo.",
};

function structuredQuoteError(data, fallback = "Request tidak dapat diproses.") {
    const visited = new Set();

    function parse(value, depth = 0) {
        if (value === null || value === undefined || depth > 5) return { reasonCode: "", message: "" };
        if (typeof value === "string") return { reasonCode: "", message: value.trim() };
        if (Array.isArray(value)) {
            const parts = value.map((item) => parse(item, depth + 1)).filter((item) => item.message || item.reasonCode);
            return {
                reasonCode: parts.find((item) => item.reasonCode)?.reasonCode || "",
                message: [...new Set(parts.map((item) => item.message).filter(Boolean))].join(", "),
            };
        }
        if (typeof value !== "object" || visited.has(value)) return { reasonCode: "", message: "" };
        visited.add(value);

        const reasonCode = normalizePaymentCode(value.reason_code || value.reasonCode || "");
        const directMessage = typeof value.message === "string" ? value.message.trim() : "";
        const nested = parse(value.detail, depth + 1);
        const validationMessage = typeof value.msg === "string" ? value.msg.trim() : "";
        return {
            reasonCode: reasonCode || nested.reasonCode,
            message: directMessage || nested.message || validationMessage || "",
        };
    }

    const parsed = parse(data);
    const forceCanonicalReasonMessage = [
        "MEMBER_LOGIN_REQUIRED",
        "GUEST_ONLY_PROMO",
        "NEW_CUSTOMER_ONLY",
        "EXISTING_CUSTOMER_ONLY",
        "CUSTOMER_NOT_TARGETED",
        "CUSTOMER_IDENTITY_REQUIRED",
        "ACCOUNT_INACTIVE",
        "ELIGIBILITY_RULE_NOT_MET",
        "ACCOUNT_TOO_OLD",
        "FIRST_PURCHASE_ONLY",
        "MIN_SUCCESSFUL_ORDERS_NOT_MET",
        "MIN_SUCCESSFUL_SPEND_NOT_MET",
        "INVALID_ELIGIBILITY_RULE",
        "PHONE_REQUIRED",
        "TARGET_ID_REQUIRED",
    ].includes(parsed.reasonCode);
    return {
        reasonCode: parsed.reasonCode,
        message: (forceCanonicalReasonMessage ? PROMO_REASON_MESSAGES[parsed.reasonCode] : "")
            || parsed.message
            || PROMO_REASON_MESSAGES[parsed.reasonCode]
            || fallback,
    };
}

function deriveCheckoutControlState({
    sku,
    method,
    phone,
    loading = false,
    scheduled = false,
    quoteReady = false,
    quoteError = "",
    applying = false,
} = {}) {
    const missingSku = !normalizeCheckoutSku(sku);
    const missingMethod = !normalizePaymentCode(method);
    const missingPhone = !isValidCheckoutPhone(phone);
    const busy = Boolean(loading || scheduled || applying);
    const disabled = missingSku || missingMethod || missingPhone || busy || !quoteReady || Boolean(quoteError);
    let message = "";
    if (applying) message = "Memvalidasi promo dan menghitung total...";
    else if (missingSku) message = "Pilih nominal dulu sebelum apply promo.";
    else if (missingMethod) message = "Pilih metode pembayaran terlebih dahulu.";
    else if (missingPhone) message = "Masukkan nomor WhatsApp yang valid sebelum apply promo.";
    else if (quoteError) message = quoteError;
    else if (loading || scheduled || !quoteReady) message = "Tunggu perhitungan harga selesai";
    return { disabled, missingSku, missingMethod, missingPhone, busy, quoteReady: Boolean(quoteReady), message };
}

function selectionPromoTransition(typedCode, currentAppliedCode) {
    const revalidateCode = normalizeVoucherCode(typedCode || currentAppliedCode);
    return {
        clearAppliedPromo: true,
        revalidateCode,
        shouldRevalidate: Boolean(revalidateCode),
    };
}

function paymentMethodPromoTransition(nextMethod, typedCode, currentAppliedCode) {
    return {
        method: normalizePaymentCode(nextMethod),
        ...selectionPromoTransition(typedCode, currentAppliedCode),
    };
}

function isQuoteResponseCurrent(requestSequence, currentSequence, expectedKey, currentKey) {
    return requestSequence === currentSequence && expectedKey === currentKey;
}

function shouldUseFreeCheckout(quote) {
    if (!quote || quote.free_checkout !== true) return false;
    return Number(quote.payment_fee || 0) <= 0 && Number(quote.total || 0) <= 0;
}

function hasAuthoritativeQuoteTotals(rawData) {
    const data = rawData?.quote || rawData?.data || rawData || {};
    const product = data.product || {};
    const hasFinite = (values) => values.some((value) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)));
    return hasFinite([
        data.base_amount,
        data.final_amount,
        data.final_price,
        data.discounted_subtotal,
        data.pricing?.final_amount,
        product.price,
    ]) && hasFinite([
        data.payment_fee,
        data.admin_fee,
        data.fee,
        data.pricing?.payment_fee,
    ]) && hasFinite([
        data.total,
        data.total_amount,
        data.payable_amount,
        data.pricing?.total,
    ]);
}

async function loadSiteBranding() {
    try {
        const res = await fetch("/api/site-settings");
        if (!res.ok) return;
        const data = await res.json();
        const siteName = data.site_name || "LIXAFA";
        const logoUrl = safeMediaUrl(data.logo_url || "");

        document.title = `${siteName} - Topup Digital Modern`;

        document.querySelectorAll("[data-site-name]").forEach((element) => {
            element.textContent = siteName;
        });

        document.querySelectorAll("[data-footer-text]").forEach((element) => {
            if (data.footer_text) element.textContent = data.footer_text;
        });

        document.querySelectorAll("[data-brand-logo]").forEach((element) => {
            element.innerHTML = "";
            if (logoUrl) {
                element.classList.add("has-image");
                const img = document.createElement("img");
                img.src = logoUrl;
                img.alt = siteName;
                element.appendChild(img);
            } else {
                element.classList.remove("has-image");
            }
        });
    } catch (err) {
        console.error("Gagal memuat branding website:", err);
    }
}

async function loadPublicPages() {
    const pageLinks = document.querySelectorAll("[data-footer-page]");
    if (!pageLinks.length) return;

    try {
        const res = await fetch("/api/pages");
        if (!res.ok) throw new Error("Gagal memuat halaman informasi");
        const data = await res.json();
        const pages = Array.isArray(data.pages) ? data.pages : [];
        const matchers = {
            about: ["tentang", "about"],
            terms: ["syarat", "ketentuan", "terms", "condition"],
            privacy: ["privasi", "privacy", "kebijakan"]
        };

        pageLinks.forEach((link) => {
            const key = link.dataset.footerPage;
            const keywords = matchers[key] || [];
            const page = pages.find((item) => {
                const haystack = `${item.slug || ""} ${item.title || ""} ${item.page_type || ""}`.toLowerCase();
                return keywords.some((keyword) => haystack.includes(keyword));
            });

            if (!page?.url) return;
            link.href = safeHref(page.url) || page.url;
            link.textContent = page.title || link.textContent.replace(" belum tersedia", "");
            link.classList.remove("is-disabled");
        });
    } catch (err) {
        console.error("Gagal memuat halaman informasi:", err);
    }
}

function escapeHtml(value) {
    return String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function formatCurrency(value) {
    return "Rp " + Number(value || 0).toLocaleString("id-ID");
}

function numberOrFallback(value, fallback = 0) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : Number(fallback || 0);
}

function formatDate(value) {
    if (!value) return "-";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return escapeHtml(value);
    return date.toLocaleString("id-ID");
}

function isPaymentChannelResponseCurrent(requestSequence, currentSequence) {
    return requestSequence === currentSequence;
}

function derivePaymentChannelLoadState({ loading = false, error = "", channels = [] } = {}) {
    const available = Array.isArray(channels)
        ? channels.filter((channel) => channel?.active === true && channel?.maintenance !== true)
        : [];
    return {
        loading: Boolean(loading),
        error: String(error || ""),
        availableCount: available.length,
        canCheckout: !loading && !error && available.length > 0,
        showRetry: !loading && Boolean(error),
    };
}

function paymentIconLabel(channel) {
    const code = normalizePaymentCode(channel.code);
    if (code === "WALLET") return `<i class="fas fa-wallet"></i> Wallet`;
    return escapeHtml(code);
}

function availablePaymentChannels() {
    return paymentChannels
        .filter((channel) => channel.active === true && channel.maintenance !== true);
}

function paymentLogoClass(code) {
    return normalizePaymentCode(code).toLowerCase().replace(/[^a-z0-9_-]/g, "");
}

function renderPublicPaymentDisplays() {
    const channels = availablePaymentChannels();
    const homeContainer = document.getElementById("homepagePaymentChannels");
    const footerContainer = document.getElementById("footerPaymentChannels");
    const visibleChannels = channels.filter((channel) => normalizePaymentCode(channel.code) !== "WALLET");

    if (homeContainer) {
        if (!visibleChannels.length) {
            const message = paymentChannelLoading
                ? "Memuat metode pembayaran..."
                : (paymentChannelLoadError || "Metode pembayaran belum tersedia.");
            homeContainer.innerHTML = `
                <div class="empty-storefront">
                    ${escapeHtml(message)}
                    ${paymentChannelLoadError ? `<button type="button" class="btn btn-sm btn-outline-warning mt-2" data-retry-payment-channels>Coba Lagi</button>` : ""}
                </div>`;
        } else {
            const grouped = {};
            visibleChannels.forEach((channel) => {
                const group = channel.group || "Pembayaran";
                if (!grouped[group]) grouped[group] = [];
                grouped[group].push(channel);
            });
            homeContainer.innerHTML = Object.entries(grouped).map(([group, items]) => `
                <div class="payment-group">
                    <div class="payment-group-title">${escapeHtml(group)}</div>
                    <div class="payment-logos">
                        ${items.map((channel) => {
                            const code = normalizePaymentCode(channel.code);
                            return `<div class="payment-logo ${escapeHtml(paymentLogoClass(code))}">${escapeHtml(channel.name || code)}</div>`;
                        }).join("")}
                    </div>
                </div>
            `).join("");
        }
    }

    if (footerContainer) {
        footerContainer.innerHTML = visibleChannels.length
            ? visibleChannels.slice(0, 12).map((channel) => `<span class="fp-logo">${escapeHtml(channel.name || channel.code || "Pembayaran")}</span>`).join("")
            : `<span class="fp-logo">${escapeHtml(paymentChannelLoading ? "Memuat..." : "Belum tersedia")}</span>`;
    }
}

function getSelectedBaseAmount() {
    const context = getCheckoutQuoteContext();
    const quote = currentPaymentQuote?.key === context.key ? currentPaymentQuote : null;
    return Number(quote?.base_amount ?? selectedPrice ?? 0);
}

function isFreeCheckoutAmount(amount = getSelectedBaseAmount()) {
    void amount;
    const context = getCheckoutQuoteContext();
    const quote = currentPaymentQuote?.key === context.key ? currentPaymentQuote : null;
    return Boolean(selectedSku) && !isSelectedPostpaid() && shouldUseFreeCheckout(quote);
}

function paymentMethodDisplay(method) {
    const normalized = normalizePaymentCode(method);
    if (normalized === "FREE_PROMO") return "Promo Gratis";
    if (normalized === "WALLET") return "Wallet LIXAFA";
    return normalized || "-";
}

function getPaymentChannelByCode(code) {
    const normalized = normalizePaymentCode(code);
    return availablePaymentChannels().find((channel) => normalizePaymentCode(channel.code) === normalized) || null;
}

function channelAvailability(channel, amount) {
    const numericAmount = Number(amount || 0);
    const min = Number(channel?.minimum_amount || 0);
    const max = Number(channel?.maximum_amount || 0);

    if (channel?.active === false) {
        return { enabled: false, note: "Tidak tersedia" };
    }
    if (!numericAmount || numericAmount <= 0) {
        return { enabled: true, note: "" };
    }
    if (min && numericAmount < min) {
        return { enabled: false, note: `Min ${formatCurrency(min)}` };
    }
    if (max && numericAmount > max) {
        return { enabled: false, note: `Maks ${formatCurrency(max)}` };
    }
    return { enabled: true, note: "" };
}

function paymentFeeLabel(channel, amount) {
    const method = normalizePaymentCode(channel?.code);
    if (!selectedSku) return "Pilih nominal";
    if (method === "WALLET") return "Tanpa admin";
    const context = getCheckoutQuoteContext();
    const quote = currentPaymentQuote?.key === context.key ? currentPaymentQuote : null;
    if (quote && method === context.method) {
        const quotedFee = Math.max(0, Number(quote.payment_fee || 0));
        return quotedFee > 0 ? `+ ${formatCurrency(quotedFee)}` : "Tanpa admin";
    }
    return "Dihitung server";
}

function renderPaymentChannels() {
    const container = document.getElementById("payment-options-container");
    if (!container) return;
    const amount = getSelectedBaseAmount();
    const currentMethodInput = document.getElementById("method");

    if (isFreeCheckoutAmount(amount)) {
        container.innerHTML = `
            <div class="payment-group-title-txn">Promo Gratis</div>
            <div class="payment-option-txn active" role="status" aria-label="Promo gratis, pembayaran tidak diperlukan">
                <div class="payment-option-left-txn">
                    <span class="payment-option-logo-txn"><i class="fas fa-gift"></i> FREE</span>
                    <div class="payment-option-copy">
                        <span style="font-size:12px;">Promo Gratis</span>
                        <small class="payment-option-note">Tidak perlu pembayaran Tripay</small>
                    </div>
                </div>
                <div class="payment-option-right-txn">
                    <span class="payment-option-fee">Langsung diproses</span>
                    <div class="radio-circle"></div>
                </div>
            </div>`;
        return;
    }

    const channels = availablePaymentChannels();
    const currentMethod = normalizePaymentCode(currentMethodInput?.value);
    if (!channels.length) {
        if (currentMethodInput && currentMethodInput.value) {
            const transition = paymentMethodPromoTransition(
                "",
                document.getElementById("promo_code")?.value,
                appliedPromoCode,
            );
            currentMethodInput.value = transition.method;
            pendingPostpaidOrder = null;
            appliedPromoCode = null;
            restoreSelectedSkuPricing();
            invalidatePaymentQuote();
        }
        const message = paymentChannelLoading
            ? "Memuat metode pembayaran..."
            : (paymentChannelLoadError || "Metode pembayaran belum tersedia.");
        container.innerHTML = `
            <div class="empty-storefront">
                ${escapeHtml(message)}
                ${paymentChannelLoadError ? `<button type="button" class="btn btn-sm btn-outline-warning mt-2" data-retry-payment-channels>Coba Lagi</button>` : ""}
            </div>`;
        return;
    }
    const firstEnabled = channels.find((channel) => channelAvailability(channel, amount).enabled);
    const resolvedMethod = channels.some((channel) => normalizePaymentCode(channel.code) === currentMethod && channelAvailability(channel, amount).enabled)
        ? currentMethod
        : normalizePaymentCode(firstEnabled?.code);

    let automaticTransition = null;
    if (currentMethodInput && currentMethodInput.value !== resolvedMethod) {
        automaticTransition = paymentMethodPromoTransition(
            resolvedMethod,
            document.getElementById("promo_code")?.value,
            appliedPromoCode,
        );
        currentMethodInput.value = automaticTransition.method;
        pendingPostpaidOrder = null;
        appliedPromoCode = null;
        restoreSelectedSkuPricing();
        invalidatePaymentQuote();
    }

    const grouped = {};
    channels.forEach((channel) => {
        const group = channel.group || "Pembayaran";
        if (!grouped[group]) grouped[group] = [];
        grouped[group].push(channel);
    });
    container.innerHTML = Object.entries(grouped).map(([group, items], groupIndex) => `
        <div class="payment-group-title-txn" style="${groupIndex ? "margin-top:14px;" : ""}">${escapeHtml(group)}</div>
        ${items.map((channel, index) => {
            const code = normalizePaymentCode(channel.code);
            const availability = channelAvailability(channel, amount);
            const isActive = code === resolvedMethod && availability.enabled;
            const feeLabel = paymentFeeLabel(channel, amount);
            return `
                <button type="button" class="payment-option-txn ${isActive ? "active" : ""} ${availability.enabled ? "" : "disabled"}"
                    data-payment-method="${escapeHtml(code)}" aria-pressed="${isActive ? "true" : "false"}"
                    aria-label="${escapeHtml(`${channel.name || code}, ${feeLabel}${availability.note ? `, ${availability.note}` : ""}`)}"
                    ${availability.enabled ? "" : "disabled aria-disabled=\"true\""}>
                    <div class="payment-option-left-txn">
                        ${safeMediaUrl(channel.icon_url) ? `<img src="${escapeHtml(safeMediaUrl(channel.icon_url))}" alt="${escapeHtml(channel.name || code)}" style="height:22px;max-width:72px;object-fit:contain;">` : `<span class="payment-option-logo-txn">${paymentIconLabel(channel)}</span>`}
                        <div class="payment-option-copy">
                            <span style="font-size:12px;">${escapeHtml(channel.name || code)}</span>
                            ${availability.note ? `<small class="payment-option-note">${escapeHtml(availability.note)}</small>` : ""}
                        </div>
                    </div>
                    <div class="payment-option-right-txn">
                        <span class="payment-option-fee ${availability.enabled ? "" : "muted"}">${escapeHtml(feeLabel)}</span>
                        <div class="radio-circle"></div>
                    </div>
                </button>`;
        }).join("")}
    `).join("");
    if (automaticTransition?.method && selectedSku && !isSelectedPostpaid()) {
        scheduleBaseQuoteThenPromoRevalidation(automaticTransition.revalidateCode, 0);
    }
}

async function loadPaymentChannels() {
    if (paymentChannelAbortController) paymentChannelAbortController.abort();
    const controller = new AbortController();
    paymentChannelAbortController = controller;
    const requestSequence = ++paymentChannelRequestSequence;
    paymentChannelLoading = true;
    paymentChannelLoadError = "";
    paymentChannels = [];
    paymentChannelSource = "";
    renderPaymentChannels();
    renderPublicPaymentDisplays();
    try {
        const res = await fetch("/api/payment/channels", {
            signal: controller.signal,
            cache: "no-store",
        });
        const data = await res.json();
        if (!res.ok || data.success !== true) {
            throw new Error(data.message || "Metode pembayaran gagal dimuat dari server.");
        }
        if (!isPaymentChannelResponseCurrent(requestSequence, paymentChannelRequestSequence)) return;
        paymentChannels = Array.isArray(data.channels) ? data.channels : [];
        if (!paymentChannels.some((channel) => channel?.active === true && channel?.maintenance !== true)) {
            throw new Error("Tidak ada metode pembayaran aktif yang tersedia.");
        }
        paymentChannelSource = data.source || "";
    } catch (error) {
        if (error.name === "AbortError" || !isPaymentChannelResponseCurrent(requestSequence, paymentChannelRequestSequence)) return;
        console.error("Gagal memuat metode pembayaran:", error);
        paymentChannels = [];
        paymentChannelSource = "";
        paymentChannelLoadError = error.message || "Metode pembayaran gagal dimuat.";
    } finally {
        if (!isPaymentChannelResponseCurrent(requestSequence, paymentChannelRequestSequence)) return;
        paymentChannelLoading = false;
        paymentChannelAbortController = null;
        renderPaymentChannels();
        renderPublicPaymentDisplays();
        updateCheckoutSummary();
    }
}

async function customerFetch(url, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (customerToken) headers["customer-token"] = customerToken;
    const res = await fetch(url, { ...options, headers });
    const raw = await res.text();
    let data = {};
    if (raw) {
        try { data = JSON.parse(raw); } catch { data = { detail: raw }; }
    }
    if (!res.ok) {
        throw new Error(data.detail || data.message || "Request customer gagal.");
    }
    return data;
}

function setCustomerSession(token, customer) {
    const previousCustomerId = currentCustomer?.id || null;
    const previousPhone = document.getElementById("wa_pembeli")?.value || "";
    const transition = selectionPromoTransition(document.getElementById("promo_code")?.value, appliedPromoCode);
    customerToken = token || "";
    currentCustomer = customer || null;
    if (customerToken) localStorage.setItem("customer_token", customerToken);
    else localStorage.removeItem("customer_token");
    const navLabel = document.getElementById("customer_nav_label");
    if (navLabel) navLabel.textContent = currentCustomer ? (currentCustomer.name || "Akun") : "Akun";
    const waInput = document.getElementById("wa_pembeli");
    if (waInput && currentCustomer?.phone) waInput.value = currentCustomer.phone;
    if (selectedSku && (previousCustomerId !== (currentCustomer?.id || null) || previousPhone !== (waInput?.value || ""))) {
        appliedPromoCode = null;
        restoreSelectedSkuPricing();
        invalidatePaymentQuote();
        updateCheckoutSummary({ refresh: false });
        scheduleBaseQuoteThenPromoRevalidation(transition.revalidateCode);
    }
}

async function loadCustomerSession() {
    if (!customerToken) {
        setCustomerSession("", null);
        return;
    }
    try {
        const data = await customerFetch("/api/customer/me");
        setCustomerSession(customerToken, data.customer);
    } catch (error) {
        setCustomerSession("", null);
    }
}

async function loadStorefrontStats() {
    try {
        const res = await fetch("/api/storefront/stats");
        if (!res.ok) return;
        storefrontStats = await res.json();
        const successRate = Number(storefrontStats.success_rate || 0);
        const successOrders = Number(storefrontStats.success_orders || 0);
        const productCount = Number(storefrontStats.active_products || 0);
        const promoCount = Number(storefrontStats.active_promos || 0);
        const successRateNode = document.getElementById("storefront_success_rate");
        const successOrdersNode = document.getElementById("storefront_success_orders");
        const productCountNode = document.getElementById("storefront_product_count");
        const promoCountNode = document.getElementById("storefront_promo_count");
        if (successRateNode) successRateNode.textContent = successRate ? `${successRate.toLocaleString("id-ID")}% sukses` : "Sistem otomatis 24 jam";
        if (successOrdersNode) successOrdersNode.textContent = successOrders ? `${successOrders.toLocaleString("id-ID")} order sukses` : "Garansi status transaksi";
        if (productCountNode) productCountNode.textContent = productCount ? `${productCount.toLocaleString("id-ID")} produk aktif` : "Katalog aktif";
        if (promoCountNode) promoCountNode.textContent = promoCount ? `${promoCount.toLocaleString("id-ID")} promo aktif` : "Voucher dan campaign";
    } catch (error) {
        console.error("Gagal memuat statistik storefront:", error);
    }
}

window.customerLogin = async function() {
    try {
        const data = await fetch("/api/customer/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                phone: document.getElementById("customer_login_phone").value,
                password: document.getElementById("customer_login_password").value
            })
        }).then(async (res) => {
            const json = await res.json();
            if (!res.ok) throw new Error(json.detail || "Login customer gagal.");
            return json;
        });
        setCustomerSession(data.token, data.customer);
        await window.loadCustomerAccount();
    } catch (error) {
        Swal.fire("Login Gagal", error.message, "error");
    }
}

window.customerRegister = async function() {
    try {
        const data = await fetch("/api/customer/register", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                name: document.getElementById("customer_register_name").value,
                phone: document.getElementById("customer_register_phone").value,
                email: document.getElementById("customer_register_email").value,
                password: document.getElementById("customer_register_password").value
            })
        }).then(async (res) => {
            const json = await res.json();
            if (!res.ok) throw new Error(json.detail || "Registrasi customer gagal.");
            return json;
        });
        setCustomerSession(data.token, data.customer);
        await window.loadCustomerAccount();
    } catch (error) {
        Swal.fire("Registrasi Gagal", error.message, "error");
    }
}

window.customerLogout = function() {
    setCustomerSession("", null);
    window.loadCustomerAccount();
}

window.createWalletOpenPayment = async function() {
    if (!customerToken) {
        Swal.fire("Login Diperlukan", "Login customer dulu untuk membuat kode deposit wallet.", "warning");
        return;
    }
    const amountPrompt = await Swal.fire({
        title: "Nominal Deposit",
        input: "number",
        inputLabel: "Masukkan nominal saldo yang ingin ditambahkan",
        inputPlaceholder: "Contoh: 50000",
        inputAttributes: { min: 1000, step: 1000 },
        showCancelButton: true,
        confirmButtonText: "Buat Invoice",
        cancelButtonText: "Batal",
        background: "#111111",
        color: "#f5f5f5",
        confirmButtonColor: "#C19B32",
        inputValidator: (value) => {
            const amount = Number(value || 0);
            if (!amount || amount < 1000) return "Minimal deposit Rp 1.000.";
            return null;
        }
    });
    if (!amountPrompt.isConfirmed) return;

    const amount = Math.floor(Number(amountPrompt.value || 0));
    try {
        const data = await customerFetch("/api/customer/wallet/open-payment", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ method: "QRIS", amount })
        });
        const amountInfo = data.type === "invoice"
            ? `<div class="small text-muted mt-3">Saldo masuk: <strong>Rp ${Number(data.amount || 0).toLocaleString("id-ID")}</strong><br>Biaya payment: Rp ${Number(data.payment_fee || 0).toLocaleString("id-ID")}<br>Total bayar: <strong>Rp ${Number(data.total || 0).toLocaleString("id-ID")}</strong></div>`
            : "";
        Swal.fire({
            title: data.type === "invoice" ? "Invoice Deposit Wallet" : (data.reused ? "Kode Deposit Wallet" : "Deposit Wallet Dibuat"),
            html: `${renderPaymentGuide(data)}${amountInfo}<p class="small text-muted mt-3 mb-0">Setelah pembayaran berhasil, saldo otomatis masuk lewat callback. Jika belum masuk, tekan Sync Saldo.</p>`,
            background: "#111111",
            color: "#f5f5f5",
            confirmButtonColor: "#C19B32"
        });
    } catch (error) {
        Swal.fire("Gagal", error.message, "error");
    }
}

window.syncWalletOpenPayment = async function() {
    if (!customerToken) return;
    try {
        const data = await customerFetch("/api/customer/wallet/sync-open-payment", { method: "POST" });
        await window.loadCustomerAccount();
        Swal.fire("Sync Selesai", `${Number(data.credited || 0)} transaksi deposit dikreditkan.`, "success");
    } catch (error) {
        Swal.fire("Gagal Sync", error.message, "error");
    }
}

window.loadCustomerAccount = async function() {
    const authPanel = document.getElementById("customer_auth_panel");
    const dashboardPanel = document.getElementById("customer_dashboard_panel");
    if (!authPanel || !dashboardPanel) return;

    if (!customerToken) {
        authPanel.style.display = "block";
        dashboardPanel.style.display = "none";
        return;
    }

    try {
        const [me, wallet, orders, tickets] = await Promise.all([
            customerFetch("/api/customer/me"),
            customerFetch("/api/customer/wallet"),
            customerFetch("/api/customer/orders"),
            customerFetch("/api/customer/tickets")
        ]);
        currentCustomer = me.customer;
        authPanel.style.display = "none";
        dashboardPanel.style.display = "block";
        document.getElementById("customer_account_name").textContent = currentCustomer.name || "Customer";
        document.getElementById("customer_account_phone").textContent = currentCustomer.phone || "";
        document.getElementById("customer_wallet_balance").textContent = formatCurrency(wallet.balance || 0);
        if (document.getElementById("customer_profile_name")) document.getElementById("customer_profile_name").value = currentCustomer.name || "";
        if (document.getElementById("customer_profile_email")) document.getElementById("customer_profile_email").value = currentCustomer.email || "";
        if (document.getElementById("customer_profile_password")) document.getElementById("customer_profile_password").value = "";
        setCustomerSession(customerToken, { ...currentCustomer, wallet_balance: wallet.balance || 0 });

        document.getElementById("customer_order_rows").innerHTML = (orders.orders || []).length
            ? orders.orders.map((order) => `
                <tr>
                    <td><code>${escapeHtml(String(order.id || "").slice(0, 10))}</code></td>
                    <td>
                        <div class="fw-semibold">${escapeHtml(order.product_name || order.nominal || "-")}</div>
                        <small class="text-muted">${escapeHtml(order.provider || order.nominal || "-")}</small>
                    </td>
                    <td>${formatCurrency(order.amount || 0)}</td>
                    <td>${escapeHtml(order.payment_status || "-")}</td>
                    <td>${escapeHtml(order.topup_status || "-")}</td>
                    <td>${formatDate(order.created_at)}</td>
                    <td>
                        ${order.payment_status === "UNPAID" ? `<button type="button" class="btn btn-sm btn-warning fw-bold" data-track-order-id="${escapeHtml(order.id)}">Bayar</button>` : ""}
                        <button type="button" class="btn btn-sm btn-outline-light" data-track-order-id="${escapeHtml(order.id)}">Lacak</button>
                    </td>
                </tr>`).join("")
            : `<tr><td colspan="7" class="text-center text-muted py-3">Belum ada riwayat order.</td></tr>`;

        document.getElementById("customer_wallet_rows").innerHTML = (wallet.ledger || []).length
            ? wallet.ledger.map((entry) => `
                <tr>
                    <td>${escapeHtml(entry.entry_type || "-")}</td>
                    <td>${formatCurrency(entry.amount || 0)}</td>
                    <td>${formatCurrency(entry.balance_after || 0)}</td>
                    <td>${escapeHtml(entry.note || "-")}</td>
                    <td>${formatDate(entry.created_at)}</td>
                </tr>`).join("")
            : `<tr><td colspan="5" class="text-center text-muted py-3">Belum ada mutasi wallet.</td></tr>`;

        document.getElementById("customer_ticket_rows").innerHTML = (tickets.tickets || []).length
            ? tickets.tickets.map((ticket) => `
                <tr>
                    <td>#${Number(ticket.id || 0).toLocaleString("id-ID")}</td>
                    <td>${escapeHtml(ticket.subject || "-")}</td>
                    <td>${escapeHtml(ticket.status || "-")}</td>
                    <td>${escapeHtml(ticket.admin_note || "-")}</td>
                    <td>${formatDate(ticket.created_at)}</td>
                </tr>`).join("")
            : `<tr><td colspan="5" class="text-center text-muted py-3">Belum ada ticket.</td></tr>`;
    } catch (error) {
        setCustomerSession("", null);
        authPanel.style.display = "block";
        dashboardPanel.style.display = "none";
    }
}

window.saveCustomerProfile = async function() {
    try {
        const payload = {
            name: document.getElementById("customer_profile_name").value,
            email: document.getElementById("customer_profile_email").value
        };
        const password = document.getElementById("customer_profile_password").value;
        if (password) payload.password = password;

        const data = await customerFetch("/api/customer/me", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        setCustomerSession(customerToken, data.customer);
        await window.loadCustomerAccount();
        Swal.fire("Profil Disimpan", "Data akun customer berhasil diperbarui.", "success");
    } catch (error) {
        Swal.fire("Gagal", error.message, "error");
    }
}

window.trackOrderFromAccount = function(orderId) {
    const accountModal = bootstrap.Modal.getInstance(document.getElementById("customerAccountModal"));
    if (accountModal) accountModal.hide();
    const input = document.getElementById("input_cek_pesanan");
    if (input) input.value = orderId;
    const modal = new bootstrap.Modal(document.getElementById("modalCekPesanan"));
    modal.show();
    cekStatusPesanan();
}

window.createSupportTicket = async function() {
    try {
        await customerFetch("/api/support/tickets", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                order_id: document.getElementById("support_order_id").value,
                subject: document.getElementById("support_subject").value,
                message: document.getElementById("support_message").value
            })
        });
        document.getElementById("support_subject").value = "";
        document.getElementById("support_message").value = "";
        await window.loadCustomerAccount();
        Swal.fire("Ticket Terkirim", "Ticket support kamu sudah masuk ke admin.", "success");
    } catch (error) {
        Swal.fire("Gagal", error.message, "error");
    }
}

function findCheckoutProduct(sku) {
    const normalizedSku = normalizeCheckoutSku(sku);
    return products.find((item) => normalizeCheckoutSku(item.sku) === normalizedSku)
        || (selectedProviderData?.items || []).find((item) => normalizeCheckoutSku(item.sku) === normalizedSku)
        || null;
}

function handleCheckoutIdentityChange() {
    const transition = selectionPromoTransition(document.getElementById("promo_code")?.value, appliedPromoCode);
    pendingPostpaidOrder = null;
    appliedPromoCode = null;
    restoreSelectedSkuPricing();
    invalidatePaymentQuote();
    updateCheckoutSummary({ refresh: false });
    if (selectedSku && !isSelectedPostpaid()) {
        scheduleBaseQuoteThenPromoRevalidation(transition.revalidateCode);
    }
}

function bindStorefrontInteractions() {
    const promoGrid = document.getElementById("promoCampaignGrid");
    promoGrid?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-promo-index]");
        if (!button || !promoGrid.contains(button)) return;
        const promo = activePromos[Number(button.dataset.promoIndex)];
        if (promo) handlePromoCta(promo.resolved_cta_url || promo.cta_url || "");
    });

    const categoryBar = document.getElementById("categoryFilterBar");
    categoryBar?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-category]");
        if (!button || !categoryBar.contains(button)) return;
        setCategoryFilter(button.dataset.category || "ALL");
    });

    const gameList = document.getElementById("game-list");
    gameList?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-provider-name]");
        if (!button || !gameList.contains(button)) return;
        const providerName = button.dataset.providerName || "";
        if (providerName) openGameOrder(providerName);
    });

    const nominalGrid = document.getElementById("nominal-grid");
    nominalGrid?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-sku]");
        if (!button || button.disabled || !nominalGrid.contains(button)) return;
        const product = findCheckoutProduct(button.dataset.sku);
        if (!product) return;
        selectSku(
            product.sku,
            product.name,
            numberOrFallback(product.price, 0),
            numberOrFallback(product.original_price, product.price || 0),
            button,
        );
    });

    const paymentContainer = document.getElementById("payment-options-container");
    paymentContainer?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-payment-method]");
        if (!button || button.disabled || !paymentContainer.contains(button)) return;
        selectPayment(button.dataset.paymentMethod || "QRIS", button);
    });

    document.addEventListener("click", (event) => {
        const paymentRetryButton = event.target.closest("[data-retry-payment-channels]");
        if (paymentRetryButton) {
            loadPaymentChannels();
            return;
        }
        const button = event.target.closest("[data-copy-payment-value]");
        if (button) {
            copyPaymentValue(button.dataset.copyPaymentValue || "", button);
            return;
        }
        const orderButton = event.target.closest("[data-track-order-id]");
        if (orderButton) window.trackOrderFromAccount(orderButton.dataset.trackOrderId || "");
    });

    document.getElementById("productSearchInput")?.addEventListener("input", renderGameList);
    document.getElementById("applyPromoButton")?.addEventListener("click", () => window.applyPromoCode());
    document.getElementById("retryQuoteButton")?.addEventListener("click", () => retryCheckoutQuote());

    const promoInput = document.getElementById("promo_code");
    promoInput?.addEventListener("input", () => {
        const typedCode = normalizeVoucherCode(promoInput.value);
        if (appliedPromoCode && typedCode !== normalizeVoucherCode(appliedPromoCode)) {
            appliedPromoCode = null;
            restoreSelectedSkuPricing();
            const feedback = document.getElementById("promo_code_feedback");
            if (feedback) {
                feedback.textContent = typedCode ? "Kode berubah. Tekan Apply untuk memvalidasi ulang." : "";
                feedback.style.color = "var(--muted)";
            }
        }
        invalidatePaymentQuote();
        updateCheckoutSummary();
    });
    promoInput?.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
            const applyButton = document.getElementById("applyPromoButton");
            if (applyButton?.disabled || promoApplyInFlight) return;
            window.applyPromoCode();
        }
    });

    document.getElementById("wa_pembeli")?.addEventListener("input", handleCheckoutIdentityChange);
    document.getElementById("dynamic-input-container")?.addEventListener("input", (event) => {
        if (!event.target.matches("#user_id, #zone_id")) return;
        handleCheckoutIdentityChange();
        cekNicknameOtomatis();
    });
}

document.addEventListener("DOMContentLoaded", () => {
    confirmModal = new bootstrap.Modal(document.getElementById('confirmModal'));
    bindStorefrontInteractions();
    loadSiteBranding();
    loadPublicPages();
    loadPaymentChannels();
    loadProducts();
    loadActivePromos();
    loadCustomerSession();
    loadStorefrontStats();
    syncCheckoutControls();

    const lastOrderId = localStorage.getItem("last_order_id");
    console.log("Cek tagihan terakhir:", lastOrderId);

    if (lastOrderId) {
        currentOrderId = lastOrderId;
        currentOrderAccessToken = localStorage.getItem("last_order_access_token") || "";
        currentPopupStatus = null; // Reset status popup
        currentPopupPaymentKey = null;
        
        Swal.fire({
            title: 'Melacak Tagihan...',
            html: '<div class="spinner-border text-primary my-3"></div><br><p>Mohon tunggu, kami sedang melacak status pesanan terakhir.</p>',
            showConfirmButton: false,
            allowOutsideClick: false,
            allowEscapeKey: false
        });
        
        updateStatusRealtime(); 
    }
});

async function loadActivePromos() {
    const container = document.getElementById("promoCampaignGrid");
    if (container) {
        container.innerHTML = `<div class="empty-storefront">Memuat promo aktif...</div>`;
    }
    try {
        const res = await fetch("/api/promos/active");
        if (!res.ok) throw new Error("Gagal memuat promo aktif");
        const data = await res.json();
        activePromos = Array.isArray(data.promos) ? data.promos : [];
        renderPromoCampaigns();
    } catch (err) {
        console.error("Gagal memuat promo aktif:", err);
        if (container) {
            container.innerHTML = `<div class="empty-storefront">Promo belum berhasil dimuat. Silakan cek halaman promo atau coba lagi nanti.</div>`;
        }
    }
}

function renderPromoCampaigns() {
    const container = document.getElementById("promoCampaignGrid");
    if (!container) return;

    if (!activePromos.length) {
        container.innerHTML = `<div class="empty-storefront">Belum ada promo aktif yang ditampilkan saat ini.</div>`;
        return;
    }

    const campaignPromos = activePromos.slice(0, 6);
    container.innerHTML = campaignPromos.map((promo, promoIndex) => {
        const badge = promo.badge || (promo.rule_type === "price" ? "Diskon" : "Info");
        const desc = promo.description || "Promo terbatas, cek detail dan periode promo sekarang.";
        const ctaText = (promo.cta_text || "Lihat Promo").trim();
        const ctaUrl = resolvePromoActionUrl(promo.resolved_cta_url || promo.cta_url || "");
        const ctaButton = ctaUrl
            ? `<button type="button" class="btn btn-sm btn-warning mt-2" data-promo-index="${promoIndex}"
                aria-label="${escapeHtml(`${ctaText}: ${promo.title || "Promo LIXAFA"}`)}">${escapeHtml(ctaText)}</button>`
            : "";
        return `
            <div class="promo-campaign-card">
                <div>
                    <div class="promo-campaign-badge">${escapeHtml(badge)}</div>
                    <div class="promo-campaign-title">${escapeHtml(promo.title || "Promo LIXAFA")}</div>
                    <div class="promo-campaign-desc">${escapeHtml(desc)}</div>
                </div>
                <div>
                    <div class="promo-campaign-meta">
                        <span>${promo.discount_type === "percent" ? `${Number(promo.discount_value || 0).toLocaleString("id-ID")}% OFF` : (promo.discount_type === "fixed" ? `Potongan Rp ${Number(promo.discount_value || 0).toLocaleString("id-ID")}` : "Promo Spesial")}</span>
                        <span>${promo.target_scope ? `Target: ${escapeHtml(promo.target_scope)}` : ""}</span>
                    </div>
                    ${ctaButton}
                </div>
            </div>`;
    }).join("");
}

window.handlePromoCta = function(rawUrl) {
    const ctaUrl = resolvePromoActionUrl(rawUrl);
    if (!ctaUrl) return;

    if (ctaUrl.startsWith("#")) {
        const sectionId = ctaUrl.slice(1);
        if (sectionId) {
            scrollToSection(sectionId);
        }
        return;
    }

    if (ctaUrl.startsWith("modal:")) {
        const modalId = ctaUrl.slice("modal:".length);
        const modalElement = document.getElementById(modalId);
        if (modalElement) {
            const modal = new bootstrap.Modal(modalElement);
            modal.show();
        }
        return;
    }

    if (ctaUrl.startsWith("game:")) {
        const provider = ctaUrl.slice("game:".length).trim();
        if (provider) {
            openGameOrder(provider);
        }
        return;
    }

    if (ctaUrl === "order" || ctaUrl === "/order") {
        const section = document.getElementById("produk-section");
        if (section) {
            showHome();
            section.scrollIntoView({ behavior: "smooth", block: "start" });
        }
        return;
    }

    const safeUrl = safeHref(ctaUrl);
    if (safeUrl && !safeUrl.startsWith("#")) {
        window.open(safeUrl, "_blank", "noopener");
        return;
    }

    const section = document.getElementById(ctaUrl);
    if (section) {
        scrollToSection(ctaUrl);
    }
}

async function loadProducts() {
    const container = document.getElementById("game-list");
    if (container) {
        container.innerHTML = `<div class="empty-storefront">Memuat katalog produk...</div>`;
    }
    try {
        const res = await fetch("/api/products");
        if (!res.ok) throw new Error("Gagal mengambil data produk");
        const data = await res.json();
        if (Array.isArray(data)) {
            products = data;
            productCategories = [];
        } else {
            products = Array.isArray(data.products) ? data.products : [];
            productCategories = Array.isArray(data.categories) ? data.categories : [];
        }
        providerIndex = {};
        if (productCategories.length > 0) {
            productCategories.forEach(category => {
                (category.providers || []).forEach(provider => {
                    providerIndex[(provider.name || "").toLowerCase()] = provider;
                });
            });
        } else {
            const grouped = {};
            products.forEach(product => {
                const providerName = product.provider || "Lainnya";
                if (!grouped[providerName]) {
                    grouped[providerName] = {
                        name: providerName,
                        logo_url: product.logo_url || "",
                        image_url: product.image_url || "",
                        description: product.description || "",
                        promo_title: product.promo_title || "",
                        promo_text: product.promo_text || "",
                        promo_badge: product.promo_badge || "",
                        promo_url: product.promo_url || "",
                        items: [],
                    };
                }
                grouped[providerName].items.push(product);
            });
            Object.values(grouped).forEach(provider => {
                providerIndex[provider.name.toLowerCase()] = provider;
            });
        }
        renderGameList();
    } catch (e) {
        console.error("Error load products:", e);
        if (container) {
            container.innerHTML = `<div class="empty-storefront">Katalog produk belum berhasil dimuat. Silakan muat ulang halaman atau coba lagi beberapa saat lagi.</div>`;
        }
    }
}

function normalizeUrl(url) {
    return resolvePromoActionUrl(url);
}

function hasUnsafeUrlCharacters(value) {
    return /[\\\u0000-\u001F\u007F]/.test(value) || /%(?:0[0-9a-f]|1[0-9a-f]|7f)/i.test(value);
}

function isProtocolRelativeUrl(value) {
    return /^\/\//.test(value);
}

function safeWebUrl(rawUrl, { allowHash = false, allowRelative = true, externalHttpsOnly = true } = {}) {
    const value = String(rawUrl || "").trim();
    if (!value || hasUnsafeUrlCharacters(value) || isProtocolRelativeUrl(value)) return "";
    if (allowHash && /^#[A-Za-z][A-Za-z0-9_.:-]*$/.test(value)) return value;

    let candidate = value;
    const hasScheme = /^[A-Za-z][A-Za-z0-9+.-]*:/.test(candidate);
    if (!hasScheme) {
        if (!allowRelative) return "";
        candidate = candidate.startsWith("/") ? candidate : `/${candidate.replace(/^\/+/, "")}`;
    }

    try {
        const parsed = new URL(candidate, window.location.origin);
        if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) return "";
        const isSameOrigin = parsed.origin === window.location.origin;
        if (!isSameOrigin && externalHttpsOnly && parsed.protocol !== "https:") return "";
        if (isSameOrigin) return `${parsed.pathname || "/"}${parsed.search || ""}${parsed.hash || ""}`;
        return parsed.href;
    } catch (_) {
        return "";
    }
}

function resolvePromoActionUrl(rawUrl) {
    const value = String(rawUrl || "").trim();
    if (!value || hasUnsafeUrlCharacters(value) || isProtocolRelativeUrl(value)) return "";
    if (/^#[A-Za-z][A-Za-z0-9_.:-]*$/.test(value)) return value;
    if (/^modal:[A-Za-z][A-Za-z0-9_.:-]*$/.test(value)) return value;
    if (value.startsWith("game:") && value.slice("game:".length).trim()) return value;
    if (value === "order" || value === "/order") return "order";
    return safeWebUrl(value, { allowHash: true, allowRelative: true, externalHttpsOnly: true });
}

function safeHref(url) {
    return safeWebUrl(url, { allowHash: true, allowRelative: true, externalHttpsOnly: true });
}

function safeMediaUrl(url) {
    return safeWebUrl(url, { allowHash: false, allowRelative: true, externalHttpsOnly: true });
}

function formatPaymentExpiry(value) {
    if (!value) return "";
    const raw = String(value).trim();
    let date = null;
    if (/^\d+$/.test(raw)) {
        const numeric = Number(raw);
        date = new Date((raw.length <= 10 ? numeric * 1000 : numeric));
    } else {
        date = new Date(raw);
    }
    if (!date || Number.isNaN(date.getTime())) return raw;
    return date.toLocaleString("id-ID");
}

function normalizeInstructionSteps(value) {
    if (!value) return [];
    if (Array.isArray(value)) return value;
    if (typeof value === "string") return [value];
    if (typeof value === "object") {
        return value.steps || value.step || value.instructions || value.content || value.description || [];
    }
    return [];
}

function instructionText(value) {
    if (!value) return "";
    if (typeof value === "string") return value;
    if (typeof value === "object") {
        return value.title || value.step || value.description || value.content || value.text || value.name || JSON.stringify(value);
    }
    return String(value);
}

function renderPaymentInstructions(instructions) {
    const items = Array.isArray(instructions) ? instructions : [];
    if (!items.length) return "";

    return `
        <div class="mt-3 text-start">
            <div class="small fw-bold text-warning mb-2">Instruksi Pembayaran</div>
            <div class="accordion accordion-flush" id="paymentInstructionAccordion">
                ${items.map((item, index) => {
                    const title = escapeHtml(instructionText(item) || `Instruksi ${index + 1}`);
                    const steps = normalizeInstructionSteps(item);
                    const stepsHtml = steps.length
                        ? `<ol class="mb-0 ps-3">${steps.map((step) => `<li class="mb-1">${escapeHtml(instructionText(step))}</li>`).join("")}</ol>`
                        : `<div class="small text-muted">${title}</div>`;
                    return `
                        <div class="accordion-item bg-transparent text-light border-secondary">
                            <h2 class="accordion-header">
                                <button class="accordion-button ${index ? "collapsed" : ""} bg-dark text-light py-2" type="button" data-bs-toggle="collapse" data-bs-target="#paymentInstruction${index}">
                                    ${title}
                                </button>
                            </h2>
                            <div id="paymentInstruction${index}" class="accordion-collapse collapse ${index ? "" : "show"}" data-bs-parent="#paymentInstructionAccordion">
                                <div class="accordion-body small text-light">
                                    ${stepsHtml}
                                </div>
                            </div>
                        </div>`;
                }).join("")}
            </div>
        </div>`;
}

async function copyPaymentValue(value, button) {
    const text = String(value || "");
    if (!text) return;
    try {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(text);
        } else {
            const input = document.createElement("textarea");
            input.value = text;
            input.style.position = "fixed";
            input.style.opacity = "0";
            document.body.appendChild(input);
            input.focus();
            input.select();
            document.execCommand("copy");
            input.remove();
        }
        if (button) {
            const original = button.textContent;
            button.textContent = "Tersalin";
            setTimeout(() => { button.textContent = original || "Salin"; }, 1200);
        }
    } catch (_) {
        Swal.fire("Gagal", "Kode belum bisa disalin otomatis.", "warning");
    }
}

function renderPaymentGuide(data = {}) {
    const paymentName = data.payment_name || data.payment_method || data.method || "Pembayaran";
    const paymentMethod = data.payment_method || data.method || "";
    const total = Number(data.total || data.amount || 0);
    const payCode = String(data.pay_code || "").trim();
    const qrUrl = safeMediaUrl(data.qr_url);
    const qrString = String(data.qr_string || "").trim();
    const payUrl = safeHref(data.pay_url || "");
    const expiredAt = formatPaymentExpiry(data.payment_expired_at || data.expired_time || data.expired_at);
    const instructions = Array.isArray(data.payment_instructions) ? data.payment_instructions : [];

    const rows = [
        ["Metode", paymentName || paymentMethod || "-"],
        ["Total", total ? formatCurrency(total) : ""],
        ["Batas Bayar", expiredAt],
    ].filter(([, value]) => value);

    const qrHtml = qrUrl ? `
        <div class="text-center mt-3">
            <img src="${escapeHtml(qrUrl)}" alt="QRIS" style="width:240px;max-width:100%;border-radius:12px;background:#fff;padding:10px;">
        </div>` : "";

    const payCodeHtml = payCode ? `
        <div class="mt-3 text-start">
            <div class="small text-muted mb-1">Kode Pembayaran</div>
            <div class="d-flex gap-2 align-items-stretch">
                <code class="flex-grow-1 p-2 rounded bg-dark text-warning border border-warning-subtle" style="white-space:normal;word-break:break-all;">${escapeHtml(payCode)}</code>
                <button type="button" class="btn btn-sm btn-warning fw-bold" data-copy-payment-value="${escapeHtml(payCode)}">Salin</button>
            </div>
        </div>` : "";

    const qrStringHtml = qrString && !qrUrl ? `
        <div class="mt-3 text-start">
            <div class="small text-muted mb-1">QRIS Payload</div>
            <div class="d-flex gap-2 align-items-stretch">
                <code class="flex-grow-1 p-2 rounded bg-dark text-warning border border-warning-subtle" style="white-space:normal;word-break:break-all;">${escapeHtml(qrString)}</code>
                <button type="button" class="btn btn-sm btn-warning fw-bold" data-copy-payment-value="${escapeHtml(qrString)}">Salin</button>
            </div>
        </div>` : "";

    const payUrlHtml = payUrl && !qrHtml && !payCodeHtml && !qrStringHtml ? `
        <div class="mt-3">
            <a href="${escapeHtml(payUrl)}" target="_blank" rel="noopener" class="btn btn-outline-warning fw-bold w-100">Buka Aplikasi Pembayaran</a>
        </div>` : "";

    const rowsHtml = rows.map(([label, value]) => `
        <div class="d-flex justify-content-between gap-3 mb-1">
            <span style="color:#aeb7c4;">${escapeHtml(label)}</span>
            <strong class="text-end" style="color:#f6f7fb;">${escapeHtml(value)}</strong>
        </div>`).join("");

    const emptyHtml = !qrHtml && !payCodeHtml && !qrStringHtml && !payUrlHtml
        ? `<div class="alert alert-warning mt-3 mb-0">Detail pembayaran belum tersedia. Cek status beberapa saat lagi.</div>`
        : "";

    return `
        <div class="payment-guide mt-3 p-3 rounded border border-warning-subtle" style="background:rgba(255,255,255,0.03);">
            ${rowsHtml ? `<div class="small text-start">${rowsHtml}</div>` : ""}
            ${qrHtml}
            ${payCodeHtml}
            ${qrStringHtml}
            ${payUrlHtml}
            ${emptyHtml}
            ${renderPaymentInstructions(instructions)}
        </div>`;
}

window.copyPaymentValue = copyPaymentValue;

function getProviderData(provider) {
    return providerIndex[(provider || "").toLowerCase()] || null;
}

function getImageUrl(provider) {
    const data = getProviderData(provider);
    if (data) {
        const candidate = data.logo_url || data.image_url;
        const safeCandidate = safeMediaUrl(candidate);
        if (safeCandidate) return safeCandidate;
    }
    const key = (provider || "").toLowerCase();
    for (let k in imageDb) { if (key.includes(k)) return imageDb[k]; }
    return imageDb["default"];
}

function formatShortCurrency(value) {
    const number = Number(value || 0);
    if (number >= 1000000) return `Rp ${(number / 1000000).toLocaleString("id-ID", { maximumFractionDigits: 1 })}jt`;
    if (number >= 1000) return `Rp ${(number / 1000).toLocaleString("id-ID", { maximumFractionDigits: 0 })}rb`;
    return formatCurrency(number);
}

function providerMatchesSearch(provider, searchTerm) {
    if (!searchTerm) return true;
    const haystack = [
        provider.name,
        provider.description,
        provider.promo_title,
        provider.promo_text,
        ...(provider.items || []).flatMap((item) => [item.name, item.sku, item.category])
    ].join(" ").toLowerCase();
    return haystack.includes(searchTerm);
}

function setCategoryFilter(categoryName) {
    activeCategoryFilter = categoryName || "ALL";
    renderGameList();
}

window.setCategoryFilter = setCategoryFilter;

function renderCategoryFilters(categories) {
    const container = document.getElementById("categoryFilterBar");
    if (!container) return;
    const buttons = [
        `<button type="button" class="category-filter-btn ${activeCategoryFilter === "ALL" ? "active" : ""}"
            data-category="ALL" aria-pressed="${activeCategoryFilter === "ALL" ? "true" : "false"}">Semua</button>`,
        ...categories.map((category) => {
            const categoryName = category.name || "";
            return `<button type="button" class="category-filter-btn ${activeCategoryFilter === categoryName ? "active" : ""}"
                data-category="${escapeHtml(categoryName)}" aria-pressed="${activeCategoryFilter === categoryName ? "true" : "false"}">${escapeHtml(categoryName || "Kategori")}</button>`;
        })
    ];
    container.innerHTML = buttons.join("");
}

function renderProviderCard(provider) {
    const heroImage = safeMediaUrl(provider.logo_url || provider.image_url || getImageUrl(provider.name)) || safeMediaUrl(getImageUrl(provider.name));
    const badgeLabel = provider.promo_badge || (Number(provider.order_count || 0) > 0 ? "Popular" : "");
    const badge = badgeLabel ? `<span class="game-badge">${escapeHtml(badgeLabel)}</span>` : "";
    const minPrice = provider.min_price !== null && provider.min_price !== undefined ? Number(provider.min_price || 0) : 0;
    const orderCount = Number(provider.order_count || 0);
    const itemCount = Number(provider.product_count || (provider.items || []).length || 0);
    return `
        <div class="col-6 col-md-4 col-lg-3">
            <button type="button" class="game-card" data-provider-name="${escapeHtml(provider.name || "")}" aria-label="Pilih ${escapeHtml(provider.name || "Produk")}">
                <img src="${escapeHtml(heroImage)}" alt="${escapeHtml(provider.name || "Produk")}">
                ${badge}
                <div class="game-title">${escapeHtml(provider.name || "Produk")}</div>
                <div class="game-card-meta">
                    <span>${minPrice ? `Mulai ${formatShortCurrency(minPrice)}` : `${itemCount} item`}</span>
                    <span>${orderCount ? `${orderCount.toLocaleString("id-ID")} order` : `${itemCount} item`}</span>
                </div>
            </button>
        </div>`;
}

function renderGameList() {
    const container = document.getElementById("game-list");
    container.innerHTML = "";
    const searchTerm = (document.getElementById("productSearchInput")?.value || "").trim().toLowerCase();

    if (productCategories.length > 0) {
        renderCategoryFilters(productCategories);
        let renderedCount = 0;
        productCategories
            .filter((category) => activeCategoryFilter === "ALL" || category.name === activeCategoryFilter)
            .forEach(category => {
            const providers = (Array.isArray(category.providers) ? category.providers : [])
                .filter((provider) => providerMatchesSearch(provider, searchTerm));
            if (!providers.length) return;
            renderedCount += providers.length;

            container.innerHTML += `
                <div class="game-category-block">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <div>
                            <div class="text-uppercase small text-muted fw-bold">Kategori</div>
                            <h5 class="mb-0 text-white">${escapeHtml(category.name || "Kategori")}</h5>
                        </div>
                        <span class="badge rounded-pill text-bg-dark border border-warning-subtle">${providers.length} provider</span>
                    </div>
                    <div class="row g-3">
                        ${providers.map(renderProviderCard).join("")}
                    </div>
                </div>`;
        });
        if (!renderedCount) {
            container.innerHTML = `<div class="empty-storefront">Produk tidak ditemukan. Coba kata kunci atau kategori lain.</div>`;
        }
        return;
    }

    renderCategoryFilters([]);
    const providerMap = {};
    products.forEach((product) => {
        const providerName = product.provider || "Lainnya";
        if (!providerMap[providerName]) {
            providerMap[providerName] = {
                name: providerName,
                logo_url: product.logo_url || "",
                image_url: product.image_url || "",
                description: product.description || "",
                promo_badge: product.promo_badge || "",
                product_count: 0,
                order_count: 0,
                min_price: null,
                items: []
            };
        }
        providerMap[providerName].product_count += 1;
        providerMap[providerName].order_count += Number(product.order_count || 0);
        providerMap[providerName].min_price = providerMap[providerName].min_price === null
            ? Number(product.price || 0)
            : Math.min(providerMap[providerName].min_price, Number(product.price || 0));
        providerMap[providerName].items.push(product);
    });
    const providers = Object.values(providerMap).filter((provider) => providerMatchesSearch(provider, searchTerm));
    if (!providers.length) {
        container.innerHTML = `<div class="empty-storefront">Produk tidak ditemukan. Coba kata kunci lain.</div>`;
        return;
    }
    container.innerHTML = `
        <div class="game-category-block">
            <div class="row g-3">
                ${providers.map(renderProviderCard).join("")}
            </div>
        </div>`;
}

function openGameOrder(provider) {
    invalidatePaymentQuote();
    selectedProvider = provider;
    selectedProviderData = getProviderData(provider);
    selectedSku = null;
    selectedItemName = null;
    selectedPrice = null;
    selectedProduct = null;
    pendingPostpaidOrder = null;
    window.selectedOriginalPrice = 0;
    appliedPromoCode = null;
    lastConfirmedQuoteKey = "";
    document.getElementById("game-title").innerText = provider;
    document.getElementById("game-image").src = getImageUrl(provider);
    const gameMeta = document.getElementById("game-meta");
    if (gameMeta) {
        gameMeta.innerText = selectedProviderData?.description || "";
        gameMeta.style.display = selectedProviderData?.description ? "block" : "none";
    }
    
    const inputContainer = document.getElementById("dynamic-input-container");
    const providerItems = selectedProviderData?.items || products.filter(p => p.provider === provider);
    const providerIsPostpaid = providerItems.length > 0 && providerItems.every((item) => String(item.product_type || "prepaid").toLowerCase() === "postpaid");
    if (providerIsPostpaid) {
        inputContainer.innerHTML = `
            <div class="col-12"><label class="visually-hidden" for="user_id">Nomor pelanggan</label><input type="text" id="user_id" class="form-control form-control-lg bg-light" placeholder="Masukkan nomor pelanggan" autocomplete="off"></div>
        `;
    } else if (provider.toLowerCase().includes("mobile legends")) {
        inputContainer.innerHTML = `
            <div class="col-7"><label class="visually-hidden" for="user_id">User ID</label><input type="text" inputmode="numeric" id="user_id" class="form-control form-control-lg bg-light" placeholder="User ID" autocomplete="off"></div>
            <div class="col-5"><label class="visually-hidden" for="zone_id">Zone ID</label><input type="text" inputmode="numeric" id="zone_id" class="form-control form-control-lg bg-light" placeholder="Zone ID" autocomplete="off"></div>
        `;
    } else {
        inputContainer.innerHTML = `
            <div class="col-12"><label class="visually-hidden" for="user_id">ID game</label><input type="text" id="user_id" class="form-control form-control-lg bg-light" placeholder="Masukkan ID Game" autocomplete="off"></div>
        `;
    }
    
    document.getElementById("nickname-box").style.display = "none";
    document.getElementById("player-nickname").innerText = "";
    const homeView = document.getElementById("home-view");
    const orderView = document.getElementById("order-view");
    homeView.style.display = "none";
    homeView.classList.remove("active");
    orderView.style.display = "block";
    orderView.classList.add("active");
    const breadcrumb = document.getElementById("breadcrumb-game");
    if (breadcrumb) breadcrumb.textContent = provider;
    const promoInput = document.getElementById("promo_code");
    const retainedPromoCode = normalizeVoucherCode(promoInput?.value);
    if (promoInput && retainedPromoCode) promoInput.value = retainedPromoCode;
    const feedback = document.getElementById("promo_code_feedback");
    if (feedback) feedback.innerText = retainedPromoCode ? "Pilih nominal untuk memvalidasi ulang promo." : "";
    window.scrollTo(0, 0);
    renderNominals(provider);
    updateCheckoutSummary();
}

function resetCheckoutState() {
    invalidatePaymentQuote();
    selectedProvider = null;
    selectedProviderData = null;
    selectedSku = null;
    selectedItemName = null;
    selectedPrice = null;
    selectedProduct = null;
    pendingPostpaidOrder = null;
    appliedPromoCode = null;
    lastConfirmedQuoteKey = "";
    updateCheckoutSummary();
}

window.resetCheckoutState = resetCheckoutState;

function showHome() {
    document.getElementById("order-view").style.display = "none";
    document.getElementById("home-view").style.display = "block";
    resetCheckoutState();
    window.scrollTo(0, 0);
}

function renderNominals(provider) {
    const grid = document.getElementById("nominal-grid");
    grid.innerHTML = "";
    const items = selectedProviderData?.items || products.filter(p => p.provider === provider);
    if (!items.length) {
        grid.innerHTML = `<div class="col-12"><div class="empty-storefront">Nominal belum tersedia untuk provider ini.</div></div>`;
        return;
    }
    items.forEach(p => {
        const productType = String(p.product_type || "prepaid").toLowerCase();
        const isPostpaid = productType === "postpaid";
        const providerAvailable = p.buyer_product_status !== false && p.seller_product_status !== false;
        const hasStock = isPostpaid || p.unlimited_stock || p.stock === null || p.stock === undefined || Number(p.stock || 0) > 0;
        const disabled = !providerAvailable || !hasStock;
        const effectivePrice = Number(p.price || 0);
        const originalPrice = Number(p.original_price || p.price || 0);
        const hasDiscount = originalPrice > effectivePrice;
        const promoBadge = p.promo_applied?.badge || p.promo_badge || "";
        const orderCount = Number(p.order_count || 0);
        grid.innerHTML += `
            <div class="col-6 col-md-4">
                <button type="button" class="nominal-card ${disabled ? "opacity-50" : ""}" data-sku="${escapeHtml(p.sku || "")}"
                    aria-pressed="false" aria-label="${escapeHtml(`${p.name || "Nominal"}, ${isPostpaid ? "cek tagihan" : formatCurrency(effectivePrice)}`)}"
                    ${disabled ? "disabled aria-disabled=\"true\"" : ""}>
                    <div class="name">${escapeHtml(p.name)}</div>
                    ${hasDiscount ? `<div class="small text-decoration-line-through fw-semibold" style="color:#ff4d4f;">Rp ${originalPrice.toLocaleString('id-ID')}</div>` : ""}
                    <div class="price">${isPostpaid ? "Cek tagihan" : `Rp ${effectivePrice.toLocaleString('id-ID')}`}</div>
                    ${isPostpaid ? `<div class="small text-warning fw-semibold mt-1">Pascabayar</div>` : ""}
                    ${disabled ? `<div class="small text-danger fw-semibold mt-1">${providerAvailable ? "Stok habis" : "Provider gangguan"}</div>` : ""}
                    ${hasDiscount && promoBadge ? `<div class="small text-warning fw-semibold mt-1">${escapeHtml(promoBadge)}</div>` : ""}
                    ${orderCount ? `<div class="small text-muted mt-1">${orderCount.toLocaleString("id-ID")} order</div>` : ""}
                </button>
            </div>`;
    });
}

function selectSku(sku, name, price, originalPrice, element) {
    const transition = selectionPromoTransition(document.getElementById("promo_code")?.value, appliedPromoCode);
    invalidatePaymentQuote();
    selectedSku = normalizeCheckoutSku(sku);
    selectedItemName = name;
    selectedPrice = price;
    selectedProduct = findCheckoutProduct(selectedSku);
    pendingPostpaidOrder = null;
    lastConfirmedQuoteKey = "";
    window.selectedOriginalPrice = originalPrice;
    document.querySelectorAll(".nominal-card").forEach((el) => {
        el.classList.remove("active");
        el.setAttribute("aria-pressed", "false");
    });
    element.classList.add("active");
    element.setAttribute("aria-pressed", "true");

    appliedPromoCode = null;
    const feedback = document.getElementById("promo_code_feedback");
    if (feedback) {
        feedback.innerText = transition.shouldRevalidate ? "Tunggu perhitungan harga selesai" : "";
        feedback.style.color = "var(--muted)";
    }
    updateCheckoutSummary({ refresh: false });
    scheduleBaseQuoteThenPromoRevalidation(transition.revalidateCode);
}

function restoreSelectedSkuPricing() {
    if (!selectedSku) {
        selectedPrice = null;
        window.selectedOriginalPrice = 0;
        return;
    }
    const catalogPrice = numberOrFallback(selectedProduct?.price, selectedPrice || 0);
    const catalogOriginalPrice = numberOrFallback(selectedProduct?.original_price, catalogPrice);
    selectedPrice = catalogPrice;
    window.selectedOriginalPrice = catalogOriginalPrice;
}

function getCheckoutQuoteContext(promoCode = appliedPromoCode) {
    const uid = String(document.getElementById("user_id")?.value || "").trim();
    const zoneId = String(document.getElementById("zone_id")?.value || "").trim();
    const phone = String(document.getElementById("wa_pembeli")?.value || "").trim();
    const method = normalizePaymentCode(document.getElementById("method")?.value);
    const normalizedPromoCode = normalizeVoucherCode(promoCode);
    const normalizedSku = normalizeCheckoutSku(selectedSku);
    const targetId = zoneId ? `${uid}${zoneId}` : uid;
    const key = JSON.stringify([
        normalizedSku,
        method,
        normalizedPromoCode,
        phone,
        uid,
        zoneId,
        String(currentCustomer?.id || ""),
    ]);
    return {
        key,
        sku: normalizedSku,
        method,
        promoCode: normalizedPromoCode,
        phone,
        uid,
        zoneId,
        targetId,
    };
}

function getCurrentCheckoutQuote(promoCode = appliedPromoCode) {
    const context = getCheckoutQuoteContext(promoCode);
    return currentPaymentQuote?.key === context.key ? currentPaymentQuote : null;
}

function setPromoFeedback(message = "", color = "var(--muted)", source = "promo") {
    const feedback = document.getElementById("promo_code_feedback");
    if (!feedback) return;
    feedback.textContent = message;
    feedback.style.color = color;
    if (feedback.dataset) feedback.dataset.source = source;
}

function syncCheckoutControls({ updateFeedback = true } = {}) {
    const method = normalizePaymentCode(document.getElementById("method")?.value);
    const phone = String(document.getElementById("wa_pembeli")?.value || "").trim();
    const context = getCheckoutQuoteContext();
    const quoteReady = Boolean(currentPaymentQuote?.key === context.key);
    const state = deriveCheckoutControlState({
        sku: selectedSku,
        method,
        phone,
        loading: Boolean(paymentQuoteLoadingKey),
        scheduled: paymentQuoteRefreshScheduled,
        quoteReady,
        quoteError: paymentQuoteError,
        applying: promoApplyInFlight,
    });
    const applyButton = document.getElementById("applyPromoButton");
    if (applyButton) {
        applyButton.disabled = state.disabled;
        applyButton.setAttribute("aria-disabled", state.disabled ? "true" : "false");
    }
    const retryButton = document.getElementById("retryQuoteButton");
    if (retryButton) {
        const showRetry = Boolean(paymentQuoteError) && !state.missingSku && !state.missingMethod && !state.busy;
        retryButton.hidden = !showRetry;
        retryButton.disabled = !showRetry;
    }

    if (updateFeedback) {
        const feedback = document.getElementById("promo_code_feedback");
        const currentSource = feedback?.dataset?.source || "";
        if (state.message && (state.busy || state.missingSku || state.missingMethod || state.missingPhone || paymentQuoteError || !quoteReady)) {
            const color = paymentQuoteError ? "#ff4d4f" : (promoApplyInFlight ? "var(--gold-light)" : "var(--muted)");
            setPromoFeedback(state.message, color, "quote-state");
        } else if (feedback && currentSource === "quote-state") {
            setPromoFeedback("", "var(--muted)", "");
        }
    }
    return state;
}

function invalidatePaymentQuote({ abort = true, clearError = true, cancelRevalidation = true } = {}) {
    if (paymentQuoteRefreshTimer) {
        clearTimeout(paymentQuoteRefreshTimer);
        paymentQuoteRefreshTimer = null;
    }
    paymentQuoteRefreshScheduled = false;
    if (abort && paymentQuoteAbortController) paymentQuoteAbortController.abort();
    paymentQuoteAbortController = null;
    paymentQuoteRequestPromise = null;
    paymentQuoteLoadingKey = "";
    currentPaymentQuote = null;
    if (clearError) paymentQuoteError = "";
    if (cancelRevalidation) {
        promoRevalidationSequence += 1;
        promoRevalidationInFlight = false;
        promoApplySequence += 1;
        promoApplyInFlight = false;
    }
    lastConfirmedQuoteKey = "";
    paymentQuoteRequestSequence += 1;
    syncCheckoutControls();
}

function firstFiniteNumber(values, fallback = 0) {
    for (const value of values) {
        if (value === null || value === undefined || value === "") continue;
        const numeric = Number(value);
        if (Number.isFinite(numeric)) return numeric;
    }
    return Number(fallback || 0);
}

function quoteErrorMessage(data, fallback) {
    return structuredQuoteError(data, fallback).message;
}

async function readResponseJson(response) {
    const raw = await response.text();
    if (!raw) return {};
    try {
        return JSON.parse(raw);
    } catch (_) {
        return { detail: raw };
    }
}

function checkoutQuoteError(response, data, fallback) {
    const structured = structuredQuoteError(data, fallback);
    const error = new Error(structured.message);
    error.status = Number(response?.status || 0);
    error.payload = data;
    error.reasonCode = structured.reasonCode;
    return error;
}

function canUseLegacyQuoteFallback(error) {
    return !error?.status || [404, 405, 415, 422, 500, 501, 502, 503, 504].includes(Number(error.status));
}

async function requestCheckoutQuote(context, signal) {
    const payload = buildCheckoutQuotePayload(context);
    const headers = buildCheckoutQuoteHeaders(customerToken);
    let fallbackError = null;

    try {
        const response = await fetch("/api/promos/quote", {
            method: "POST",
            headers,
            body: JSON.stringify(payload),
            signal,
        });
        const data = await readResponseJson(response);
        if (response.ok && data.success !== false && data.valid !== false) {
            return { data, source: "promo_quote" };
        }
        const error = checkoutQuoteError(response, data, "Quote promo tidak tersedia.");
        if (!canUseLegacyQuoteFallback(error)) throw error;
        fallbackError = error;
    } catch (error) {
        if (error.name === "AbortError") throw error;
        if (!canUseLegacyQuoteFallback(error)) throw error;
        fallbackError = error;
    }

    try {
        const params = new URLSearchParams({ sku: context.sku, method: context.method });
        if (context.promoCode) params.set("promo_code", context.promoCode);
        if (context.phone) params.set("phone", context.phone);
        if (context.targetId) params.set("target_id", context.targetId);
        const legacyHeaders = {};
        if (customerToken) legacyHeaders["customer-token"] = customerToken;
        const response = await fetch(`/api/payment/quote?${params.toString()}`, { headers: legacyHeaders, signal });
        const data = await readResponseJson(response);
        if (response.ok && data.success !== false) return { data, source: "payment_quote" };
        const error = checkoutQuoteError(response, data, "Quote pembayaran tidak tersedia.");
        if (!canUseLegacyQuoteFallback(error)) throw error;
        fallbackError = error;
    } catch (error) {
        if (error.name === "AbortError") throw error;
        if (!canUseLegacyQuoteFallback(error)) throw error;
        fallbackError = error;
    }

    throw fallbackError || new Error("Total pembayaran belum tersedia dari server. Silakan coba lagi beberapa saat lagi.");
}

function promoListFromQuote(data = {}) {
    const product = data.product || {};
    const candidates = [
        ...(Array.isArray(data.promos_applied) ? data.promos_applied : []),
        ...(Array.isArray(data.applied_promos) ? data.applied_promos : []),
        ...(Array.isArray(product.promos_applied) ? product.promos_applied : []),
        ...(data.promo ? [data.promo] : []),
        ...(data.promo_applied ? [data.promo_applied] : []),
        ...(product.promo_applied ? [product.promo_applied] : []),
    ].filter((item) => item && typeof item === "object");
    const seen = new Set();
    return candidates.filter((promo) => {
        const key = String(promo.id || promo.code || promo.title || promo.badge || JSON.stringify(promo));
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

function normalizeCheckoutQuote(rawData, context, source) {
    const data = rawData?.quote || rawData?.data || rawData || {};
    const product = data.product || {};
    const fallbackFinal = Number(selectedPrice || 0);
    const explicitDiscount = firstFiniteNumber([
        data.discount_amount,
        data.discount,
        data.pricing?.discount_amount,
    ], 0);
    let baseAmount = firstFiniteNumber([
        data.base_amount,
        data.final_amount,
        data.final_price,
        data.discounted_subtotal,
        data.pricing?.final_amount,
        product.price,
    ], fallbackFinal);
    let originalAmount = firstFiniteNumber([
        data.subtotal,
        data.original_amount,
        data.original_price,
        data.pricing?.subtotal,
        product.original_price,
        window.selectedOriginalPrice,
    ], Math.max(baseAmount, fallbackFinal));
    if (!Number.isFinite(baseAmount)) baseAmount = fallbackFinal;
    if (!Number.isFinite(originalAmount) || originalAmount < baseAmount) originalAmount = baseAmount;
    const discountAmount = Math.max(0, explicitDiscount || (originalAmount - baseAmount));
    if (!data.base_amount && !data.final_amount && !data.final_price && discountAmount > 0) {
        baseAmount = Math.max(0, originalAmount - discountAmount);
    }
    const paymentFee = Math.max(0, firstFiniteNumber([
        data.payment_fee,
        data.admin_fee,
        data.fee,
        data.pricing?.payment_fee,
    ], 0));
    const total = Math.max(0, firstFiniteNumber([
        data.total,
        data.total_amount,
        data.payable_amount,
        data.pricing?.total,
    ], baseAmount + paymentFee));
    const promos = promoListFromQuote(data);
    return {
        ...data,
        key: context.key,
        source,
        base_amount: baseAmount,
        original_amount: originalAmount,
        discount_amount: discountAmount,
        payment_fee: paymentFee,
        total,
        promos_applied: promos,
        promo_applied: promos[promos.length - 1] || null,
    };
}

function promoLabelForQuote(quote, fallbackProduct = selectedProduct) {
    const promos = quote
        ? (Array.isArray(quote.promos_applied) ? quote.promos_applied : [])
        : promoListFromQuote({ product: fallbackProduct || {} });
    const labels = promos.map((promo) => promo.title || promo.badge || (promo.code ? `Kode ${promo.code}` : ""))
        .map((label) => String(label || "").trim())
        .filter(Boolean);
    if (labels.length) return [...new Set(labels)].join(" + ");
    if (quote?.discount_amount > 0) return appliedPromoCode ? `Kode ${appliedPromoCode}` : "Promo otomatis";
    if (appliedPromoCode && !quote) return `Kode ${appliedPromoCode} (memvalidasi)`;
    return "-";
}

async function refreshPaymentQuote({ force = false, promoCode, commitPromo = false, throwOnError = false } = {}) {
    if (!selectedSku || isSelectedPostpaid()) return null;
    const resolvedPromoCode = normalizeVoucherCode(promoCode === undefined ? appliedPromoCode : promoCode);
    const context = getCheckoutQuoteContext(resolvedPromoCode);
    if (!context.sku || !context.method) {
        const error = new Error("Konteks produk atau metode pembayaran belum siap.");
        error.reasonCode = "INVALID_TRANSACTION_CONTEXT";
        if (!context.promoCode) paymentQuoteError = error.message;
        updateCheckoutSummary({ refresh: false });
        syncCheckoutControls();
        if (throwOnError) throw error;
        return null;
    }
    if (!force && currentPaymentQuote?.key === context.key) return currentPaymentQuote;
    if (paymentQuoteLoadingKey === context.key && paymentQuoteRequestPromise) return paymentQuoteRequestPromise;

    if (paymentQuoteAbortController) paymentQuoteAbortController.abort();
    const controller = new AbortController();
    paymentQuoteAbortController = controller;
    const requestSequence = ++paymentQuoteRequestSequence;
    paymentQuoteLoadingKey = context.key;
    paymentQuoteRefreshScheduled = false;
    if (!context.promoCode) paymentQuoteError = "";
    const statusNode = document.getElementById("summary_status_label");
    if (statusNode) statusNode.textContent = "Menghitung...";
    syncCheckoutControls();

    const requestPromise = (async () => {
        try {
            const result = await requestCheckoutQuote(context, controller.signal);
            if (!isQuoteResponseCurrent(
                requestSequence,
                paymentQuoteRequestSequence,
                context.key,
                getCheckoutQuoteContext(resolvedPromoCode).key,
            )) return null;
            if (!hasAuthoritativeQuoteTotals(result.data)) {
                const error = new Error("Quote server belum memuat biaya dan total final. Silakan coba lagi.");
                error.reasonCode = "QUOTE_NOT_READY";
                throw error;
            }
            const quote = normalizeCheckoutQuote(result.data, context, result.source);
            if (commitPromo) appliedPromoCode = normalizeVoucherCode(context.promoCode);
            currentPaymentQuote = quote;
            paymentQuoteError = "";
            return quote;
        } catch (error) {
            if (error.name === "AbortError" || requestSequence !== paymentQuoteRequestSequence) return null;
            console.error("Gagal mengambil quote pembayaran:", error);
            if (!context.promoCode) {
                paymentQuoteError = error.message || "Gagal menghitung total pembayaran.";
                if (currentPaymentQuote?.key === context.key) currentPaymentQuote = null;
            }
            if (throwOnError) throw error;
            return null;
        } finally {
            if (requestSequence === paymentQuoteRequestSequence) {
                paymentQuoteLoadingKey = "";
                paymentQuoteAbortController = null;
                paymentQuoteRequestPromise = null;
                updateCheckoutSummary({ refresh: false });
                syncCheckoutControls();
            }
        }
    })();
    paymentQuoteRequestPromise = requestPromise;
    return requestPromise;
}

function schedulePaymentQuoteRefresh(delay = 180) {
    return scheduleBaseQuoteThenPromoRevalidation("", delay);
}

function scheduleBaseQuoteThenPromoRevalidation(promoCode = "", delay = 180) {
    if (!selectedSku || isSelectedPostpaid()) return;
    if (paymentQuoteRefreshTimer) clearTimeout(paymentQuoteRefreshTimer);
    const normalizedCode = normalizeVoucherCode(promoCode);
    paymentQuoteError = "";
    paymentQuoteRefreshScheduled = true;
    updateCheckoutSummary({ refresh: false });
    if (!normalizePaymentCode(document.getElementById("method")?.value)) {
        paymentQuoteRefreshScheduled = false;
        syncCheckoutControls();
        return;
    }
    paymentQuoteRefreshScheduled = true;
    const revalidationSequence = ++promoRevalidationSequence;
    promoRevalidationInFlight = Boolean(normalizedCode);
    syncCheckoutControls();
    paymentQuoteRefreshTimer = setTimeout(async () => {
        paymentQuoteRefreshTimer = null;
        paymentQuoteRefreshScheduled = false;
        const baseContextKey = getCheckoutQuoteContext(null).key;
        const baseQuote = await refreshPaymentQuote({ force: true, promoCode: null });
        if (revalidationSequence !== promoRevalidationSequence || !baseQuote || baseQuote.key !== baseContextKey) {
            if (revalidationSequence === promoRevalidationSequence) promoRevalidationInFlight = false;
            syncCheckoutControls();
            return;
        }
        if (!normalizedCode) {
            promoRevalidationInFlight = false;
            syncCheckoutControls();
            return;
        }
        const typedCode = normalizeVoucherCode(document.getElementById("promo_code")?.value);
        if (typedCode !== normalizedCode) {
            promoRevalidationInFlight = false;
            syncCheckoutControls();
            return;
        }
        if (!isValidCheckoutPhone(document.getElementById("wa_pembeli")?.value)) {
            promoRevalidationInFlight = false;
            setPromoFeedback("Masukkan nomor WhatsApp yang valid sebelum apply promo.", "#ff4d4f", "quote-state");
            syncCheckoutControls({ updateFeedback: false });
            return;
        }
        try {
            const quote = await refreshPaymentQuote({
                force: true,
                promoCode: normalizedCode,
                commitPromo: true,
                throwOnError: true,
            });
            if (revalidationSequence !== promoRevalidationSequence || !quote) return;
            setPromoFeedback(`Promo aktif. Hemat ${formatCurrency(quote.discount_amount)}.`, "#3CB371", "promo");
        } catch (error) {
            if (revalidationSequence !== promoRevalidationSequence || error.name === "AbortError") return;
            appliedPromoCode = null;
            restoreSelectedSkuPricing();
            setPromoFeedback(error.message || "Gagal memvalidasi kode promo.", "#ff4d4f", "promo");
        } finally {
            if (revalidationSequence === promoRevalidationSequence) {
                promoRevalidationInFlight = false;
                updateCheckoutSummary({ refresh: false });
                syncCheckoutControls({ updateFeedback: false });
            }
        }
    }, delay);
}

async function retryCheckoutQuote() {
    if (!selectedSku || !normalizePaymentCode(document.getElementById("method")?.value)) return;
    paymentQuoteError = "";
    const transition = selectionPromoTransition(document.getElementById("promo_code")?.value, appliedPromoCode);
    appliedPromoCode = null;
    restoreSelectedSkuPricing();
    scheduleBaseQuoteThenPromoRevalidation(transition.revalidateCode, 0);
}

async function ensureFreshPaymentQuote() {
    const context = getCheckoutQuoteContext();
    if (currentPaymentQuote?.key === context.key) return currentPaymentQuote;
    const quote = await refreshPaymentQuote({ force: true, throwOnError: true });
    if (!quote || quote.key !== getCheckoutQuoteContext().key) {
        throw new Error("Harga berubah saat diperiksa. Silakan coba lagi.");
    }
    return quote;
}

window.applyPromoCode = async function() {
    const promoCodeInput = document.getElementById("promo_code");
    const code = normalizeVoucherCode(promoCodeInput?.value);
    if (promoApplyInFlight) return;
    const controlState = syncCheckoutControls({ updateFeedback: false });
    if (controlState.disabled) {
        const color = paymentQuoteError || controlState.missingSku || controlState.missingMethod || controlState.missingPhone
            ? "#ff4d4f"
            : "var(--muted)";
        setPromoFeedback(controlState.message || "Tunggu perhitungan harga selesai", color, "quote-state");
        return;
    }

    if (!code) {
        appliedPromoCode = null;
        invalidatePaymentQuote();
        restoreSelectedSkuPricing();
        setPromoFeedback("", "var(--muted)", "");
        updateCheckoutSummary({ refresh: false });
        schedulePaymentQuoteRefresh(0);
        return;
    }

    if (promoCodeInput) promoCodeInput.value = code;
    const applySequence = ++promoApplySequence;
    promoApplyInFlight = true;
    promoRevalidationSequence += 1;
    promoRevalidationInFlight = false;
    setPromoFeedback("Memvalidasi promo dan menghitung total...", "var(--gold-light)", "promo");
    syncCheckoutControls({ updateFeedback: false });
    appliedPromoCode = null;
    restoreSelectedSkuPricing();

    try {
        const quote = await refreshPaymentQuote({ force: true, promoCode: code, commitPromo: true, throwOnError: true });
        if (applySequence !== promoApplySequence || !quote) return;
        updateCheckoutSummary({ refresh: false });
        setPromoFeedback(`Promo aktif. Hemat ${formatCurrency(quote.discount_amount)}.`, "#3CB371", "promo");
    } catch (error) {
        if (applySequence !== promoApplySequence || error.name === "AbortError") return;
        appliedPromoCode = null;
        restoreSelectedSkuPricing();
        updateCheckoutSummary({ refresh: false });
        setPromoFeedback(error.message || "Gagal memvalidasi kode promo.", "#ff4d4f", "promo");
    } finally {
        if (applySequence === promoApplySequence) {
            promoApplyInFlight = false;
            syncCheckoutControls({ updateFeedback: false });
        }
    }
}

function selectPayment(method, element) {
    const transition = paymentMethodPromoTransition(
        method || "QRIS",
        document.getElementById("promo_code")?.value,
        appliedPromoCode,
    );
    const methodInput = document.getElementById("method");
    if (methodInput) methodInput.value = transition.method;
    document.querySelectorAll(".payment-option-txn[data-payment-method]").forEach((node) => {
        node.classList.remove("active");
        node.setAttribute("aria-pressed", "false");
    });
    if (element) {
        element.classList.add("active");
        element.setAttribute("aria-pressed", "true");
    }
    pendingPostpaidOrder = null;
    appliedPromoCode = null;
    restoreSelectedSkuPricing();
    invalidatePaymentQuote();
    updateCheckoutSummary({ refresh: false });
    scheduleBaseQuoteThenPromoRevalidation(transition.revalidateCode);
}

let typingTimer;
function cekNicknameOtomatis() {
    clearTimeout(typingTimer);
    if (String(selectedProduct?.product_type || "prepaid").toLowerCase() === "postpaid") {
        const box = document.getElementById("nickname-box");
        const nickElement = document.getElementById("player-nickname");
        if (box) box.style.display = "none";
        if (nickElement) nickElement.dataset.name = "Pascabayar";
        return;
    }
    const uid = document.getElementById("user_id")?.value;
    const zid = document.getElementById("zone_id")?.value || "";
    // Ambil nama provider (game) yang sedang dipilih user
    const gameCode = selectedProvider || "game"; 

    if (!uid || uid.length < 4) {
        document.getElementById("nickname-box").style.display = "none";
        // Kosongkan dataset jika ID dihapus
        const nickElement = document.getElementById("player-nickname");
        if (nickElement) nickElement.dataset.name = "";
        return;
    }

    document.getElementById("nickname-box").style.display = "block";
    document.getElementById("player-nickname").innerText = "Mencari data ke server...";
    document.getElementById("player-nickname").className = "text-secondary fs-5";

    typingTimer = setTimeout(async () => {
        try {
            // Memanggil API Backend yang kita buat di topup_routes.py
            const res = await fetch("/check-nickname", { 
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ 
                    game_code: gameCode, 
                    user_id: uid, 
                    zone_id: zid 
                })
            });
            
            const data = await res.json();

            if (!res.ok) {
                // Jika API membalas ID tidak ditemukan / error
                document.getElementById("player-nickname").innerText = "ID Tidak Ditemukan";
                document.getElementById("player-nickname").className = "text-danger fw-bold fs-5";
                document.getElementById("player-nickname").dataset.name = ""; // Kosongkan dataset
            } else {
                // Jika Nickname berhasil ditemukan
                document.getElementById("player-nickname").innerText = data.nickname;
                document.getElementById("player-nickname").className = "text-success fw-bold fs-5";
                document.getElementById("player-nickname").dataset.name = data.nickname; // Simpan untuk struk
            }
        } catch (err) {
            document.getElementById("player-nickname").innerText = "Gagal menghubungi server";
            document.getElementById("player-nickname").className = "text-danger fw-bold fs-5";
            document.getElementById("player-nickname").dataset.name = "";
        }
    }, 1000); // Tunggu user selesai mengetik 1 detik sebelum hit API
}

function calculatePaymentFee(basePrice, method) {
    const price = Number(basePrice || 0);
    const normalizedMethod = normalizePaymentCode(method);
    const quoteKey = getCheckoutQuoteContext().key;
    if (currentPaymentQuote?.key === quoteKey) {
        return Number(currentPaymentQuote.payment_fee || 0);
    }
    if (price <= 0 || normalizedMethod === "FREE_PROMO") return 0;
    if (normalizedMethod === "WALLET") return 0;
    return 0;
}

function updateCheckoutSummary({ refresh = true } = {}) {
    renderPaymentChannels();
    const method = normalizePaymentCode(document.getElementById("method")?.value);
    const context = getCheckoutQuoteContext();
    const quote = currentPaymentQuote?.key === context.key ? currentPaymentQuote : null;
    const finalPrice = Number(quote?.base_amount ?? selectedPrice ?? 0);
    const fallbackOriginal = numberOrFallback(window.selectedOriginalPrice, selectedPrice || 0);
    const originalPrice = Math.max(finalPrice, Number(quote?.original_amount ?? fallbackOriginal));
    const discountAmount = Math.max(0, Number(quote?.discount_amount ?? (originalPrice - finalPrice)));
    const adminFee = quote ? Number(quote.payment_fee || 0) : 0;
    const totalPrice = quote ? Number(quote.total || 0) : 0;
    const isLoading = Boolean(paymentQuoteLoadingKey) || paymentQuoteRefreshScheduled;
    const statusLabel = !selectedSku
        ? "Belum lengkap"
        : (!method
            ? "Pilih pembayaran"
            : (paymentQuoteError
                ? "Gagal menghitung"
                : (isLoading ? "Menghitung..." : (quote ? "Siap checkout" : "Menunggu quote"))));
    const freeCheckout = shouldUseFreeCheckout(quote);
    const values = {
        summary_status_label: statusLabel,
        summary_provider: selectedProvider || "-",
        summary_item: selectedItemName || "Pilih nominal",
        summary_method: paymentMethodDisplay(freeCheckout ? "FREE_PROMO" : method),
        summary_base_price: formatCurrency(originalPrice),
        summary_discount: discountAmount > 0 ? `- ${formatCurrency(discountAmount)}` : formatCurrency(0),
        summary_fee: quote ? formatCurrency(adminFee) : "Menunggu quote",
        summary_promo: promoLabelForQuote(quote),
        summary_total: quote ? formatCurrency(totalPrice) : "Menunggu quote",
    };
    Object.entries(values).forEach(([id, value]) => {
        const node = document.getElementById(id);
        if (node) node.textContent = value;
    });
    syncCheckoutControls();
    if (refresh && selectedSku && method && !quote && !isLoading && !paymentQuoteError && !isSelectedPostpaid()) {
        schedulePaymentQuoteRefresh();
    }
}

window.updateCheckoutSummary = updateCheckoutSummary;

function isSelectedPostpaid() {
    return String(selectedProduct?.product_type || "prepaid").toLowerCase() === "postpaid";
}

async function preparePostpaidOrder(accountId, method) {
    if (pendingPostpaidOrder && pendingPostpaidOrder.sku === selectedSku && pendingPostpaidOrder.customer_no === accountId && pendingPostpaidOrder.method === method) {
        return pendingPostpaidOrder;
    }
    const waPembeli = document.getElementById("wa_pembeli")?.value || "";
    if (!waPembeli) throw new Error("Nomor WhatsApp wajib diisi.");
    const headers = { "Content-Type": "application/json" };
    if (customerToken) headers["customer-token"] = customerToken;
    const res = await fetch("/api/postpaid/inquiry", {
        method: "POST",
        headers,
        body: JSON.stringify({
            phone: waPembeli,
            customer_no: accountId,
            sku: selectedSku,
            method,
            promo_code: appliedPromoCode
        })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Cek tagihan gagal.");
    pendingPostpaidOrder = { ...data, sku: selectedSku, customer_no: accountId, method };
    return pendingPostpaidOrder;
}

async function validasiSebelumBeli() {
    const continueButton = document.getElementById("continuePaymentButton");
    // 1. Cek dulu apakah pembeli udah milih produk
    if (!selectedSku) {
        Swal.fire("Pilih Produk", "Pilih nominal top up terlebih dahulu.", "warning");
        return;
    }

    // 2. Ambil Data ID Game
    const uid = String(document.getElementById("user_id")?.value || "").trim();
    const zid = String(document.getElementById("zone_id")?.value || "").trim();
    if (!uid) {
        Swal.fire("Data Belum Lengkap", "Masukkan ID akun tujuan terlebih dahulu.", "warning");
        return;
    }
    const accountId = zid ? `${uid} (${zid})` : uid;
    const checkoutPhone = String(document.getElementById("wa_pembeli")?.value || "").trim();
    if (!isValidCheckoutPhone(checkoutPhone)) {
        Swal.fire("Nomor WhatsApp Belum Valid", "Masukkan nomor WhatsApp aktif untuk validasi promo dan notifikasi pesanan.", "warning");
        return;
    }

    // 3. CEK APAKAH NICKNAME VALID SEBELUM MELANJUTKAN (PERBAIKAN KRUSIAL)
    const nickElement = document.getElementById("player-nickname");
    let nickname = (nickElement && nickElement.dataset.name) ? nickElement.dataset.name : "";
    
    if (!nickname && !isSelectedPostpaid()) {
        Swal.fire("ID Tidak Valid", "Pastikan User ID benar dan Nama Akun sudah muncul di layar sebelum melanjutkan pembayaran.", "error");
        return; // Hentikan eksekusi, modal tidak akan muncul!
    }

    // 4. Gunakan quote server yang masih sesuai dengan seluruh konteks checkout.
    let adminFee = 0;
    let resolvedBasePrice = Number(selectedPrice || 0);
    let originalPrice = Math.max(resolvedBasePrice, numberOrFallback(window.selectedOriginalPrice, selectedPrice || 0));
    let discountAmount = Math.max(0, originalPrice - resolvedBasePrice);
    let totalPrice = resolvedBasePrice;
    let promoLabel = "-";
    let authoritativeQuote = null;
    const method = normalizePaymentCode(document.getElementById("method")?.value);
    if (!method) {
        Swal.fire("Metode Pembayaran Belum Tersedia", "Metode pembayaran belum berhasil dimuat. Silakan muat ulang halaman atau coba lagi beberapa saat lagi.", "warning");
        if (continueButton) continueButton.disabled = false;
        return;
    }
    if (continueButton) continueButton.disabled = true;

    if (isSelectedPostpaid()) {
        try {
            Swal.fire({
                title: "Cek Tagihan...",
                html: '<div class="spinner-border text-warning my-3"></div>',
                showConfirmButton: false,
                allowOutsideClick: false,
                allowEscapeKey: false
            });
            const postpaid = await preparePostpaidOrder(uid, method);
            nickname = postpaid.customer_name || "Pelanggan Pascabayar";
            resolvedBasePrice = Number(postpaid.base_amount || 0);
            originalPrice = Math.max(resolvedBasePrice, firstFiniteNumber([
                postpaid.subtotal,
                postpaid.original_amount,
                postpaid.original_price,
            ], resolvedBasePrice));
            discountAmount = Math.max(0, firstFiniteNumber([postpaid.discount_amount], originalPrice - resolvedBasePrice));
            adminFee = Number(postpaid.payment_fee || 0);
            totalPrice = Number(postpaid.total || 0);
            promoLabel = promoLabelForQuote(normalizeCheckoutQuote(postpaid, getCheckoutQuoteContext(), "postpaid"));
            Swal.close();
        } catch (error) {
            Swal.fire("Cek Tagihan Gagal", error.message, "error");
            if (continueButton) continueButton.disabled = false;
            return;
        }
    } else {
        try {
            const quote = await ensureFreshPaymentQuote();
            authoritativeQuote = quote;
            resolvedBasePrice = Number(quote.base_amount || 0);
            originalPrice = Math.max(resolvedBasePrice, Number(quote.original_amount || resolvedBasePrice));
            discountAmount = Math.max(0, Number(quote.discount_amount || (originalPrice - resolvedBasePrice)));
            adminFee = Number(quote.payment_fee || 0);
            totalPrice = Number(quote.total || 0);
            promoLabel = promoLabelForQuote(quote);
            lastConfirmedQuoteKey = quote.key;
        } catch (error) {
            Swal.fire("Harga Belum Siap", error.message || "Gagal menghitung total terbaru. Silakan coba lagi.", "error");
            if (continueButton) continueButton.disabled = false;
            return;
        }
    }

    const freeCheckout = !isSelectedPostpaid() && shouldUseFreeCheckout(authoritativeQuote);
    if (method === "WALLET" && !freeCheckout && (!customerToken || !currentCustomer)) {
        Swal.fire("Login Diperlukan", "Login akun customer dulu untuk membayar dengan Wallet.", "warning");
        if (continueButton) continueButton.disabled = false;
        return;
    }

    // 5. Isi Data ke Modal Konfirmasi
    document.getElementById("conf-game").innerText = selectedProvider;
    document.getElementById("conf-id").innerText = accountId;
    document.getElementById("conf-nick").innerText = nickname; // Nickname ditaruh di sini
    document.getElementById("conf-item").innerText = selectedItemName;
    document.getElementById("conf-method").innerText = paymentMethodDisplay(freeCheckout ? "FREE_PROMO" : method);
    
    document.getElementById("conf-base-price").innerText = formatCurrency(originalPrice);
    const discountNode = document.getElementById("conf-discount");
    if (discountNode) discountNode.innerText = discountAmount > 0 ? `- ${formatCurrency(discountAmount)}` : formatCurrency(0);
    const promoNode = document.getElementById("conf-promo");
    if (promoNode) promoNode.innerText = promoLabel;
    document.getElementById("conf-fee").innerText = formatCurrency(adminFee);
    document.getElementById("conf-price").innerText = formatCurrency(totalPrice);

    // 6. Tampilkan Modalnya ke layar
    var modal = new bootstrap.Modal(document.getElementById('confirmModal'));
    modal.show();
    if (continueButton) continueButton.disabled = false;
}

async function eksekusiBeli() {
    const btn = document.getElementById("btnEksekusi");
    btn.disabled = true;
    btn.innerText = "Memproses...";

    if (!isSelectedPostpaid() && (!lastConfirmedQuoteKey || lastConfirmedQuoteKey !== getCheckoutQuoteContext().key)) {
        btn.disabled = false;
        btn.innerText = "BAYAR SEKARANG";
        confirmModal.hide();
        Swal.fire("Data Berubah", "Data checkout berubah setelah dikonfirmasi. Periksa total terbaru lalu lanjutkan kembali.", "warning");
        updateCheckoutSummary();
        return;
    }

    const uid = String(document.getElementById("user_id")?.value || "").trim();
    const zid = String(document.getElementById("zone_id")?.value || "").trim();
    const wa_pembeli = String(document.getElementById("wa_pembeli")?.value || "").trim();
    if (!isValidCheckoutPhone(wa_pembeli)) {
        btn.disabled = false;
        btn.innerText = "BAYAR SEKARANG";
        confirmModal.hide();
        Swal.fire("Nomor WhatsApp Belum Valid", "Masukkan nomor WhatsApp aktif sebelum membuat pesanan.", "warning");
        return;
    }
    
    const target_id = zid ? `${uid}${zid}` : uid; 
    const method = normalizePaymentCode(document.getElementById("method")?.value);
    const nickname = document.getElementById("player-nickname").dataset.name || "-";
    if (!checkoutIdempotencyKey) {
        checkoutIdempotencyKey = window.crypto?.randomUUID?.() || `checkout-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }

    try {
        if (pendingPostpaidOrder) {
            rememberOrderAccess(pendingPostpaidOrder);
            currentPopupStatus = null;
            currentPopupPaymentKey = null;
            confirmModal.hide();
            if (pendingPostpaidOrder.wallet_paid) {
                await loadCustomerSession();
                Swal.fire({
                    title: 'Wallet Berhasil',
                    html: '<div class="spinner-border text-success my-3"></div><br><p>Saldo wallet terpotong. Tagihan sedang diproses.</p>',
                    showConfirmButton: false,
                    allowOutsideClick: false,
                    allowEscapeKey: false
                });
            } else {
                showStatusResult("pending_payment", pendingPostpaidOrder);
            }
            updateStatusRealtime();
            return;
        }

        const requestHeaders = { "Content-Type": "application/json" };
        if (customerToken) requestHeaders["customer-token"] = customerToken;
        requestHeaders["Idempotency-Key"] = checkoutIdempotencyKey;
        const res = await fetch("/topup", {
            method: "POST",
            headers: requestHeaders,
            body: JSON.stringify({ 
                phone: wa_pembeli,
                target_id: target_id,
                provider: selectedProvider, 
                nominal: selectedSku, 
                promo_code: appliedPromoCode,
                nickname: nickname,
                method 
            })
        });
        
        const data = await res.json();
        if (!res.ok) throw new Error(quoteErrorMessage(data, "Checkout gagal diproses."));

        checkoutIdempotencyKey = "";

        rememberOrderAccess(data);
        currentPopupStatus = null; // Reset status popup
        currentPopupPaymentKey = null;
        
        confirmModal.hide();
        
        if (data.wallet_paid) {
            await loadCustomerSession();
            Swal.fire({
                title: 'Wallet Berhasil',
                html: '<div class="spinner-border text-success my-3"></div><br><p>Saldo wallet terpotong. Pesanan sedang diproses.</p>',
                showConfirmButton: false,
                allowOutsideClick: false,
                allowEscapeKey: false
            });
            updateStatusRealtime();
            return;
        }

        if (data.free_checkout) {
            showStatusResult("processing", data);
            updateStatusRealtime();
            return;
        }

        showStatusResult("pending_payment", data);
        updateStatusRealtime(); 
        
    } catch (err) {
        Swal.fire("Gagal", err.message, "error");
    } finally {
        btn.disabled = false;
        btn.innerText = "BAYAR SEKARANG";
    }
}

async function updateStatusRealtime() {
    if (!currentOrderId) return;
    try {
        const res = await fetch("/topup/" + currentOrderId, { headers: orderAccessHeaders() });
        
        if (!res.ok) {
            if (currentPopupStatus !== "error") {
                Swal.fire('Error', 'Data transaksi hilang dari server!', 'error');
                currentPopupStatus = "error";
            }
            clearRememberedOrder();
            return; 
        }

        const data = await res.json();
        const stage = (data.stage || "pending_payment").toLowerCase();
        
        if (stage === "success") { 
            if (currentPopupStatus !== "success") {
                Swal.fire({
                    title: 'Berhasil!',
                    text: 'Pesanan Anda telah masuk ke akun!',
                    icon: 'success',
                    background: '#111111',
                    color: '#f5f5f5',
                    confirmButtonColor: '#C19B32',
                    confirmButtonText: 'Tutup'
                }).then(() => { location.reload(); }); // Langsung refresh kalau di-close
                currentPopupStatus = "success";
            }
            clearRememberedOrder();
        } else if (stage === "failed") {
            if (currentPopupStatus !== "failed") {
                Swal.fire({
                    title: 'Dibatalkan',
                    text: 'Pembayaran gagal atau telah kadaluarsa.',
                    icon: 'error',
                    background: '#111111',
                    color: '#f5f5f5',
                    confirmButtonColor: '#C19B32',
                    confirmButtonText: 'Tutup'
                }).then(() => { location.reload(); });
                currentPopupStatus = "failed";
            }
            clearRememberedOrder();
        } else if (stage === "processing") {
            showStatusResult("processing", data);
            setTimeout(updateStatusRealtime, 5000);
        } else if (stage === "provider_pending") {
            showStatusResult("provider_pending", data);
            setTimeout(updateStatusRealtime, 5000);
        } else if (stage === "pending_payment") {
            showStatusResult("pending_payment", data);
            setTimeout(updateStatusRealtime, 5000);
        } else {
            showStatusResult("processing", data);
            setTimeout(updateStatusRealtime, 5000);
        }
    } catch(e) { 
        setTimeout(updateStatusRealtime, 5000); 
    }
}

function showStatusResult(status, orderData = {}) {
    const paymentKey = [
        orderData.id || currentOrderId || "",
        orderData.payment_name || "",
        orderData.pay_code || "",
        orderData.qr_url || "",
        orderData.qr_string || "",
        orderData.payment_expired_at || orderData.expired_time || "",
    ].join("|");
    if (currentPopupStatus === status && currentPopupPaymentKey === paymentKey) return;
    currentPopupStatus = status;
    currentPopupPaymentKey = paymentKey;

    if (status === "pending_payment") {
        Swal.fire({
            title: 'Selesaikan Pembayaran',
            html: `
                ${renderPaymentGuide(orderData)}
                <button onclick="updateStatusRealtime()" class="btn btn-warning rounded-pill px-4 py-2 mt-3 fw-bold w-100">
                    <i class="bi bi-arrow-repeat"></i> Cek Status
                </button>
                <button onclick="batalkanTransaksi()" class="btn btn-outline-danger rounded-pill px-4 py-2 mt-3 fw-bold w-100">
                    <i class="bi bi-x-circle"></i> Batalkan & Buat Pesanan Baru
                </button>
                <p class="small text-muted mt-3">Popup ini akan otomatis berubah jika pembayaran sudah terdeteksi.</p>
            `,
            showConfirmButton: false,
            allowOutsideClick: false,
            allowEscapeKey: false,
            background: '#111111',
            color: '#f5f5f5'
        });
    } else if (status === "processing") {
        Swal.fire({
            title: 'Menunggu Pesanan Dikirim',
            html: `
                <div class="spinner-border text-success my-3" role="status" style="width: 3rem; height: 3rem;"></div><br>
                <h5 class="text-success fw-bold mb-2">Pembayaran diterima, pesanan sedang dikirim ke provider.</h5>
                <p class="small text-muted">Mohon tunggu sebentar, pesanan sedang dikirim ke provider dan akan otomatis berubah jika sudah masuk.</p>
            `,
            showConfirmButton: false,
            allowOutsideClick: false,
            allowEscapeKey: false,
            background: '#111111',
            color: '#f5f5f5'
        });
    } else if (status === "provider_pending") {
        Swal.fire({
            title: 'Menunggu Provider',
            html: `
                <div class="spinner-border text-warning my-3" role="status" style="width: 3rem; height: 3rem;"></div><br>
                <h5 class="text-warning fw-bold mb-2">Pesanan sudah dikirim ke provider</h5>
                <p class="small text-muted">Sekarang sistem menunggu konfirmasi dari provider. Silakan tunggu, status akan berubah otomatis saat sukses.</p>
            `,
            showConfirmButton: false,
            allowOutsideClick: false,
            allowEscapeKey: false,
            background: '#111111',
            color: '#f5f5f5'
        });
    }
}

// FITUR BARU: BATALKAN TRANSAKSI
// FITUR BARU: BATALKAN TRANSAKSI (SAMPAI KE DATABASE)
window.batalkanTransaksi = function() {
    Swal.fire({
        title: 'Batalkan Pesanan?',
        text: "Anda yakin ingin membatalkan tagihan ini dan memilih nominal lain?",
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#d33',
        cancelButtonColor: '#6c757d',
        confirmButtonText: 'Ya, Batalkan!',
        cancelButtonText: 'Kembali'
    }).then(async (result) => { // PERBAIKAN: Tambah async di sini
        if (result.isConfirmed) {
            
            // 1. LAPOR KE SERVER BUAT GANTI STATUS DATABASE JADI CANCELED
            if (currentOrderId) {
                try {
                    const cancelResponse = await fetch(`/topup/${currentOrderId}/cancel`, {
                        method: "POST",
                        headers: orderAccessHeaders(),
                    });
                    if (!cancelResponse.ok) {
                        const payload = await cancelResponse.json().catch(() => ({}));
                        throw new Error(payload.detail || "Transaksi tidak bisa dibatalkan.");
                    }
                } catch(e) {
                    console.error("Gagal lapor ke server", e);
                    Swal.fire("Tidak Bisa Dibatalkan", e.message || "Transaksi sudah tidak bisa dibatalkan.", "warning");
                    currentPopupStatus = null;
                    currentPopupPaymentKey = null;
                    updateStatusRealtime();
                    return;
                }
            }

            // 2. Hapus ingatan tagihan dari browser
            clearRememberedOrder();
            currentPopupPaymentKey = null;
            
            Swal.fire({
                title: 'Dibatalkan!',
                text: 'Pesanan telah dibatalkan.',
                icon: 'success',
                timer: 1500,
                showConfirmButton: false
            }).then(() => {
                location.reload(); // Refresh halaman biar bersih total
            });
        } else {
            // Kalau gajadi batal, pancing popup tagihannya biar muncul lagi
            currentPopupStatus = null; 
            currentPopupPaymentKey = null;
            updateStatusRealtime();
        }
    });
}

// FITUR CEK PESANAN
async function cekStatusPesanan() {
    const identifier = document.getElementById("input_cek_pesanan").value.trim();
    const resultBox = document.getElementById("hasil_cek_pesanan");

    if (!identifier) {
        alert("Masukkan Order ID terlebih dahulu.");
        return;
    }

    // Kasih efek loading biar keren
    resultBox.classList.remove("d-none");
    resultBox.innerHTML = `<div class="text-center"><div class="spinner-border text-warning spinner-border-sm"></div> Mencari data...</div>`;

    try {
        const response = await fetch(`/topup/${identifier}`, { headers: orderAccessHeaders(identifier) });
        const data = await response.json();

        if (!response.ok) {
            resultBox.innerHTML = `<div class="text-danger fw-bold"><i class="bi bi-x-circle"></i> ${escapeHtml(data.detail || "Pesanan tidak ditemukan")}</div>`;
            return;
        }

        // Tentukan warna badge status
        let badgeColor = "bg-secondary";
        let statusText = data.status;
        
        if (statusText === "SUCCESS") badgeColor = "bg-success";
        else if (statusText === "UNPAID") badgeColor = "bg-danger";
        else if (statusText === "PAID" || statusText === "PROCESSING") {
            badgeColor = "bg-warning text-dark";
            statusText = "DIPROSES";
        } else if (statusText === "FAILED") badgeColor = "bg-dark border border-danger text-danger";

        const timeline = Array.isArray(data.timeline) ? data.timeline : [];
        const timelineHtml = timeline.length ? `
            <div class="mt-3">
                ${timeline.map((step) => `
                    <div class="d-flex gap-2 align-items-start mb-2">
                        <span class="badge ${step.done ? "bg-success" : (step.active ? "bg-warning text-dark" : "bg-secondary")}">${step.done ? "OK" : (step.active ? "..." : "-")}</span>
                        <div>
                            <div class="fw-semibold">${escapeHtml(step.label || "-")}</div>
                            <div class="small text-muted">${escapeHtml(step.description || "")}</div>
                        </div>
                    </div>
                `).join("")}
            </div>` : "";

        // Susun tampilan hasil
        let htmlResult = `
            <div class="d-flex justify-content-between align-items-center border-bottom border-dark pb-2 mb-2">
                <span class="small text-muted">Status:</span>
                <span class="badge ${badgeColor}">${escapeHtml(statusText || "-")}</span>
            </div>
            <div class="small">
                <div class="d-flex justify-content-between mb-1"><span class="text-muted">Order ID</span><code>${escapeHtml(data.id || "-")}</code></div>
                <div class="d-flex justify-content-between mb-1"><span class="text-muted">Produk</span><strong>${escapeHtml(data.product_name || data.nominal_name || "-")}</strong></div>
                <div class="d-flex justify-content-between mb-1"><span class="text-muted">Target</span><span>${escapeHtml(data.target_id || "-")}</span></div>
                <div class="d-flex justify-content-between mb-1"><span class="text-muted">Metode</span><span>${escapeHtml(data.payment_name || data.payment_method || "-")}</span></div>
                <div class="d-flex justify-content-between mb-1"><span class="text-muted">Total</span><strong>${formatCurrency(data.amount || 0)}</strong></div>
                ${data.sn ? `<div class="d-flex justify-content-between mb-1"><span class="text-muted">SN</span><span>${escapeHtml(data.sn)}</span></div>` : ""}
                ${data.note ? `<div class="mt-2 text-muted">${escapeHtml(data.note)}</div>` : ""}
            </div>
            ${timelineHtml}
        `;

        if (data.can_pay || data.payment_status === "UNPAID") {
            htmlResult += `
                <div class="mt-3">
                    <p class="small mb-2">Pesanan belum dibayar, selesaikan pembayaran dari detail berikut:</p>
                    ${renderPaymentGuide(data)}
                </div>
            `;
        }

        resultBox.innerHTML = htmlResult;

    } catch (error) {
        resultBox.innerHTML = `<div class="text-danger fw-bold">Gagal menghubungi server.</div>`;
    }
}

window.__lixafaCheckoutFrontend = Object.freeze({
    normalizeCheckoutSku,
    normalizePaymentCode,
    normalizeVoucherCode,
    isValidCheckoutPhone,
    buildCheckoutQuotePayload,
    buildCheckoutQuoteHeaders,
    structuredQuoteError,
    deriveCheckoutControlState,
    selectionPromoTransition,
    paymentMethodPromoTransition,
    isQuoteResponseCurrent,
    isPaymentChannelResponseCurrent,
    derivePaymentChannelLoadState,
    shouldUseFreeCheckout,
    hasAuthoritativeQuoteTotals,
    normalizeCheckoutQuote,
});
