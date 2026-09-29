const token = localStorage.getItem("admin_token");
if (!token) {
    window.location.href = "/admin";
}

let addModal;
let editModal;
let promoAddModal;
let promoEditModal;
let adminAddModal;
let adminEditModal;
let pageAddModal;
let pageEditModal;

let allGroupedProducts = {};
let promoState = [];
let promoOptions = null;
let promoDetailModal;
let promoUsageModal;
const promoViewState = {
    page: 1,
    pageSize: 10,
    search: "",
    status: "",
    type: "",
    scope: "",
    sort: "updated_desc"
};
let adminState = [];
let pageState = [];
let currentAdmin = null;
let currentSiteName = "LIXAFA PROJEK";
const APP_TIME_ZONE = "Asia/Jakarta";
const PROMO_CUSTOMER_SEGMENT_DEFAULTS = [
    { value: "all", label: "Semua pelanggan", description: "Promo dapat digunakan oleh guest maupun member yang login.", aliases: ["all_customers"] },
    { value: "members_only", label: "Khusus member terdaftar", description: "Promo hanya dapat digunakan oleh pelanggan yang sudah masuk ke akun aktif.", aliases: ["member"] },
    { value: "guests_only", label: "Khusus guest", description: "Promo hanya dapat digunakan tanpa login akun.", aliases: ["guest"] },
    { value: "new", label: "Pembeli pertama", description: "Guest atau member yang belum pernah memiliki transaksi berhasil.", aliases: [] },
    { value: "existing", label: "Pelanggan lama", description: "Guest atau member yang sudah pernah memiliki minimal satu transaksi berhasil.", aliases: [] },
    { value: "specific", label: "Pelanggan tertentu", description: "Promo hanya dapat digunakan oleh pelanggan yang dipilih admin.", aliases: [] }
];
const PROMO_ELIGIBILITY_OPERATOR_LABELS = {
    equals: "Sama dengan",
    not_equals: "Tidak sama dengan",
    greater_than: "Lebih dari",
    greater_than_or_equal: "Minimal",
    less_than: "Kurang dari",
    less_than_or_equal: "Maksimal",
    in: "Termasuk dalam",
    not_in: "Tidak termasuk dalam",
    is_true: "Bernilai ya",
    is_false: "Bernilai tidak",
    between: "Di antara"
};
const PROMO_ELIGIBILITY_FALLBACK = {
    version: 1,
    max_conditions: 20,
    max_bytes: 16384,
    group_operators: [
        { value: "all", label: "Penuhi SEMUA syarat" },
        { value: "any", label: "Penuhi SALAH SATU syarat" }
    ],
    fields: [
        { value: "authentication_status", label: "Status autentikasi", type: "enum", operators: ["equals", "not_equals", "in", "not_in"], options: [{ value: "guest", label: "Guest" }, { value: "member", label: "Member" }] },
        { value: "account_status", label: "Status akun", type: "enum", operators: ["equals", "not_equals", "in", "not_in"], options: [{ value: "active", label: "Aktif" }, { value: "inactive", label: "Nonaktif" }] },
        { value: "has_customer_account", label: "Memiliki akun customer", type: "boolean", operators: ["is_true", "is_false", "equals", "not_equals"] },
        { value: "account_age_days", label: "Umur akun", type: "integer", unit: "hari", min: 0, operators: ["equals", "not_equals", "greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal", "between"] },
        { value: "account_created_at", label: "Tanggal akun dibuat", type: "datetime", operators: ["equals", "not_equals", "greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal", "between"] },
        { value: "successful_order_count", label: "Jumlah transaksi berhasil", type: "integer", min: 0, operators: ["equals", "not_equals", "greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal", "between"] },
        { value: "successful_order_total", label: "Total transaksi berhasil", type: "money", min: 0, operators: ["equals", "not_equals", "greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal", "between"] },
        { value: "days_since_last_successful_order", label: "Hari sejak transaksi berhasil terakhir", type: "integer", unit: "hari", min: 0, operators: ["equals", "not_equals", "greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal", "between"] },
        { value: "has_successful_order", label: "Pernah transaksi berhasil", type: "boolean", operators: ["is_true", "is_false", "equals", "not_equals"] },
        { value: "customer_id", label: "Akun customer tertentu", type: "customer", operators: ["equals", "not_equals", "in", "not_in"] },
        { value: "normalized_phone", label: "Nomor WhatsApp ternormalisasi", type: "string", operators: ["equals", "not_equals", "in", "not_in"] },
        { value: "target_id", label: "Target transaksi", type: "string", operators: ["equals", "not_equals", "in", "not_in"] }
    ],
    presets: [
        { value: "all", label: "Semua pelanggan", description: "Tidak ada syarat identitas khusus.", rules: { version: 1, operator: "all", conditions: [] } },
        { value: "members_only", label: "Khusus member terdaftar", description: "Member yang login dengan akun aktif.", rules: { version: 1, operator: "all", conditions: [{ field: "authentication_status", operator: "equals", value: "member" }, { field: "account_status", operator: "equals", value: "active" }] } },
        { value: "guests_only", label: "Khusus guest", description: "Pengguna yang checkout tanpa login akun.", rules: { version: 1, operator: "all", conditions: [{ field: "authentication_status", operator: "equals", value: "guest" }] } },
        { value: "first_purchase", label: "Pembeli pertama", description: "Guest atau member yang belum pernah memiliki transaksi berhasil.", rules: { version: 1, operator: "all", conditions: [{ field: "successful_order_count", operator: "equals", value: 0 }] } },
        { value: "member_new", label: "Member baru", description: "Member aktif dengan umur akun maksimal 7 hari. Syarat transaksi pertama dapat ditambahkan admin.", rules: { version: 1, operator: "all", conditions: [{ field: "authentication_status", operator: "equals", value: "member" }, { field: "account_status", operator: "equals", value: "active" }, { field: "account_age_days", operator: "less_than_or_equal", value: 7 }] } },
        { value: "existing", label: "Pelanggan lama", description: "Guest atau member yang sudah pernah memiliki minimal satu transaksi berhasil.", rules: { version: 1, operator: "all", conditions: [{ field: "successful_order_count", operator: "greater_than_or_equal", value: 1 }] } },
        { value: "specific", label: "Pelanggan tertentu", description: "Hanya akun customer yang dipilih admin.", rules: { version: 1, operator: "all", conditions: [{ field: "customer_id", operator: "in", value: [] }] } },
        { value: "custom", label: "Custom / Aturan sendiri", description: "Susun sendiri kombinasi syarat pelanggan.", rules: null }
    ]
};
const promoCustomerSelections = new Map();
const promoCustomerSearchTimers = new Map();
const promoCustomerSearchSequences = new Map();
const promoCustomerRemoteResults = new Map();
const promoEligibilityStates = new Map();
let promoEligibilityConditionSequence = 0;

let rolePermissions = {
    owner: ["orders:view", "orders:manage", "products:manage", "promos:manage", "admins:manage", "content:manage", "settings:manage", "api:monitor", "finance:view", "customers:manage", "audit:view"],
    manager: ["orders:view", "orders:manage", "products:manage", "promos:manage", "content:manage", "finance:view", "customers:manage"],
    marketing: ["promos:manage", "content:manage"],
    support: ["orders:view"],
    catalog: ["products:manage"]
};

let permissionLabels = {
    "orders:view": "Melihat transaksi dan statistik",
    "orders:manage": "Retry, update status, dan refund order",
    "products:manage": "Mengelola produk dan sinkronisasi provider",
    "promos:manage": "Mengelola promo dan kode voucher",
    "admins:manage": "Mengelola akun admin dan hak akses",
    "content:manage": "Mengelola halaman/konten website",
    "settings:manage": "Mengelola logo dan identitas website",
    "api:monitor": "Melihat status API Tripay, Digiflazz, dan engine transaksi",
    "finance:view": "Melihat laporan dan export transaksi",
    "customers:manage": "Mengelola customer dan blacklist",
    "audit:view": "Melihat audit log admin"
};

const roleLabels = {
    owner: "Owner",
    manager: "Manager",
    marketing: "Marketing",
    support: "Support",
    catalog: "Catalog"
};

const viewOrder = [
    { permission: "orders:view", loader: () => window.loadOrders() },
    { permission: "finance:view", loader: () => window.loadReports() },
    { permission: "products:manage", loader: () => window.loadProducts() },
    { permission: "promos:manage", loader: () => window.loadPromos() },
    { permission: "customers:manage", loader: () => window.loadCustomers() },
    { permission: "admins:manage", loader: () => window.loadAdmins() },
    { permission: "content:manage", loader: () => window.loadPages() },
    { permission: "settings:manage", loader: () => window.loadSettings() },
    { permission: "audit:view", loader: () => window.loadAuditLogs() },
    { permission: "api:monitor", loader: () => window.loadApiMonitor() }
];

function byId(id) {
    return document.getElementById(id);
}

function getModal(id) {
    const node = byId(id);
    return node ? new bootstrap.Modal(node) : null;
}

function escapeHtml(value) {
    return String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function escapeJs(value) {
    return String(value || "")
        .replace(/\\/g, "\\\\")
        .replace(/'/g, "\\'")
        .replace(/\r/g, "")
        .replace(/\n/g, "\\n");
}

function safeHref(url) {
    const value = String(url || "").trim();
    if (!value) return "";
    try {
        const parsed = new URL(value, window.location.origin);
        if (["http:", "https:"].includes(parsed.protocol)) return parsed.href;
    } catch (_) {
        return "";
    }
    return "";
}

function safeMediaUrl(url) {
    return safeHref(url);
}

function validatePromoCtaUrl(rawUrl, allowExternal = false) {
    const value = String(rawUrl || "").trim();
    if (!value) return { valid: true, value: "", external: false };
    if (/[\\\u0000-\u001f\u007f]/.test(value) || value.startsWith("//")) {
        return { valid: false, reason: "CTA URL tidak boleh memakai protocol-relative, backslash, atau karakter kontrol." };
    }
    if (/^#[A-Za-z][\w:.-]*$/.test(value)) return { valid: true, value, external: false };
    if (/^game:[^<>"']+$/i.test(value) || /^modal:[A-Za-z][\w:.-]*$/i.test(value)) {
        return { valid: true, value, external: false };
    }
    if (value.startsWith("/")) return { valid: true, value, external: false };
    try {
        const parsed = new URL(value, window.location.origin);
        if (!["http:", "https:"].includes(parsed.protocol)) {
            return { valid: false, reason: "CTA hanya mendukung path internal atau URL HTTP(S)." };
        }
        const external = parsed.origin !== window.location.origin;
        if (external && !allowExternal) {
            return { valid: false, reason: "Aktifkan izin URL eksternal sebelum menyimpan CTA di luar website." };
        }
        return {
            valid: true,
            value: external ? parsed.href : `${parsed.pathname}${parsed.search}${parsed.hash}`,
            external
        };
    } catch (_) {
        return { valid: false, reason: "Format CTA URL tidak valid." };
    }
}

function numericValue(id, fallback = 0) {
    const value = byId(id)?.value;
    if (value === "" || value === null || value === undefined) return fallback;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
}

const promoNumberFieldLabels = {
    discount_value: "Nilai Diskon",
    max_discount: "Maksimum Diskon",
    minimum_transaction: "Minimum Transaksi",
    special_price: "Harga Khusus",
    usage_limit: "Kuota Total",
    quota_daily: "Kuota Harian",
    budget_limit: "Batas Anggaran",
    max_per_customer: "Batas per Pelanggan",
    max_per_customer_daily: "Batas per Hari",
    max_per_phone: "Batas per Nomor WA",
    max_per_target: "Batas per Target ID",
    max_promotions_per_order: "Maksimum Promo per Order",
    priority: "Prioritas",
    display_order: "Urutan"
};

function promoFieldLabel(fieldName) {
    return promoNumberFieldLabels[fieldName] || fieldName;
}

function normalizeNumberText(value) {
    return String(value).trim().replace(/[^\d-]/g, "");
}

function parseOptionalInteger(value, label = "Field angka") {
    if (value === "" || value === null || value === undefined) return null;
    if (typeof value === "number") {
        if (!Number.isFinite(value)) throw new Error(`${label} harus berupa angka.`);
        if (!Number.isInteger(value)) throw new Error(`${label} harus berupa angka bulat.`);
        if (value < 0) throw new Error(`${label} tidak boleh negatif.`);
        return value;
    }
    const text = String(value).trim();
    if (!text) return null;
    if (!/^-?\d+$/.test(text)) throw new Error(`${label} harus berupa angka bulat.`);
    const parsed = Number(text);
    if (!Number.isSafeInteger(parsed)) throw new Error(`${label} harus berupa angka bulat yang valid.`);
    if (parsed < 0) throw new Error(`${label} tidak boleh negatif.`);
    return parsed;
}

function parseOptionalDecimal(value, label = "Field angka") {
    if (value === "" || value === null || value === undefined) return null;
    if (typeof value === "number") {
        if (!Number.isFinite(value)) throw new Error(`${label} harus berupa angka.`);
        if (value < 0) throw new Error(`${label} tidak boleh negatif.`);
        return value;
    }
    const text = String(value).trim();
    if (!text) return null;
    if (!/^-?\d+(?:[.,]\d+)?$/.test(text)) throw new Error(`${label} harus berupa angka.`);
    const normalized = text.replace(",", ".");
    const parsed = Number(normalized);
    if (!Number.isFinite(parsed)) throw new Error(`${label} harus berupa angka.`);
    if (parsed < 0) throw new Error(`${label} tidak boleh negatif.`);
    return parsed;
}

function parseOptionalIntegerField(id, fieldName) {
    return parseOptionalInteger(byId(id)?.value, promoFieldLabel(fieldName));
}

function parseOptionalDecimalField(id, fieldName) {
    return parseOptionalDecimal(byId(id)?.value, promoFieldLabel(fieldName));
}

function optionalPromoText(value) {
    const text = String(value ?? "").trim();
    return text || null;
}

function listValue(value) {
    if (Array.isArray(value)) return value.filter((item) => item !== null && item !== undefined && String(item).trim() !== "");
    if (value === null || value === undefined || value === "") return [];
    if (typeof value === "string") {
        try {
            const parsed = JSON.parse(value);
            if (Array.isArray(parsed)) return parsed;
        } catch (_) {
            return value.split(",").map((item) => item.trim()).filter(Boolean);
        }
    }
    return [value];
}

const promoDayNameToNumber = {
    sun: 0,
    sunday: 0,
    min: 0,
    minggu: 0,
    mon: 1,
    monday: 1,
    sen: 1,
    senin: 1,
    tue: 2,
    tuesday: 2,
    sel: 2,
    selasa: 2,
    wed: 3,
    wednesday: 3,
    rab: 3,
    rabu: 3,
    thu: 4,
    thursday: 4,
    kam: 4,
    kamis: 4,
    fri: 5,
    friday: 5,
    jum: 5,
    jumat: 5,
    sat: 6,
    saturday: 6,
    sab: 6,
    sabtu: 6
};

const validationFieldLabels = {
    ...promoNumberFieldLabels,
    target_ids: "Target Produk",
    product_ids: "Produk",
    category_ids: "Kategori",
    game_ids: "Game",
    provider_ids: "Provider",
    target_option_ids: "Target Promo",
    excluded_target_option_ids: "Pengecualian Target",
    excluded_target_ids: "Produk Pengecualian",
    customer_ids: "Target Pelanggan",
    customer_target_ids: "Target Pelanggan",
    promotion_target_ids: "Target Promo",
    payment_method_ids: "Metode Pembayaran",
    active_days: "Hari Aktif",
    placements: "Penempatan",
    payment_methods: "Metode Pembayaran",
    promo_type: "Jenis Promo",
    lifecycle_status: "Status Promo",
    rounding_rule: "Aturan Pembulatan",
    code: "Kode Voucher",
    title: "Nama Promo",
    starts_at: "Waktu Mulai",
    ends_at: "Waktu Berakhir",
    daily_start_time: "Jam Mulai Harian",
    daily_end_time: "Jam Selesai Harian"
};

function humanizeField(field) {
    if (!field) return "";
    return String(field)
        .replace(/_ids$/i, "")
        .replace(/_id$/i, "")
        .replace(/_/g, " ")
        .replace(/\b\w/g, (char) => char.toUpperCase());
}

function getValidationField(loc) {
    if (!Array.isArray(loc)) return null;
    const ignored = new Set(["body", "query", "path", "form"]);
    for (let index = loc.length - 1; index >= 0; index -= 1) {
        const segment = loc[index];
        if (typeof segment === "string" && !ignored.has(segment)) return segment;
    }
    return null;
}

function getValidationIndex(loc) {
    if (!Array.isArray(loc)) return null;
    for (let index = loc.length - 1; index >= 0; index -= 1) {
        if (typeof loc[index] === "number") return loc[index];
    }
    return null;
}

function formatValidationError(error) {
    const field = getValidationField(error?.loc);
    const itemIndex = getValidationIndex(error?.loc);
    const label = validationFieldLabels[field] || humanizeField(field);
    let prefix = label || "Data";
    if (itemIndex !== null) prefix += ` item ke-${itemIndex + 1}`;

    const type = String(error?.type || "");
    const message = String(error?.msg || error?.message || "").replace(/^Value error,\s*/i, "");
    const lowered = message.toLowerCase();
    if (type.includes("int_") || lowered.includes("valid integer") || lowered.includes("integer")) {
        if (itemIndex !== null && ["target_option_ids", "excluded_target_option_ids", "customer_ids"].includes(field)) {
            return `${prefix} memiliki ID tidak valid.`;
        }
        return `${prefix} harus berupa angka bulat.`;
    }
    if (type.includes("decimal") || type.includes("float") || lowered.includes("valid number") || lowered.includes("valid decimal")) {
        return `${prefix} harus berupa angka.`;
    }
    if (type === "missing" || type.endsWith(".missing")) {
        return `${prefix} wajib diisi.`;
    }
    if (type === "greater_than_equal" && Number(error?.ctx?.ge) === 0) {
        return `${prefix} tidak boleh negatif.`;
    }
    return message ? `${prefix}: ${message}` : `${prefix} tidak valid.`;
}

function parseIntegerArray(values, label = "Item") {
    if (!Array.isArray(values)) return [];
    return values.map((value, index) => {
        const raw = typeof value === "object" && value !== null ? value.id : value;
        if (raw === "" || raw === null || raw === undefined) {
            throw new Error(`${label} item ke-${index + 1} tidak memiliki ID.`);
        }
        const parsed = Number(raw);
        if (!Number.isInteger(parsed) || parsed <= 0) {
            throw new Error(`${label} item ke-${index + 1} memiliki ID tidak valid.`);
        }
        return parsed;
    });
}

function parseStringArray(values) {
    if (!Array.isArray(values)) return [];
    return values
        .map((value) => {
            const raw = typeof value === "object" && value !== null
                ? value.code ?? value.value ?? value.sku
                : value;
            return String(raw ?? "").trim();
        })
        .filter(Boolean);
}

if (typeof window !== "undefined") {
    window.__lixafaPromoValidation = {
        getValidationField,
        getValidationIndex,
        formatValidationError,
        detailToMessage,
        parseIntegerArray,
        parseStringArray,
        normalizePromoActiveDays,
        canonicalPromoType,
        canonicalPromoCustomerSegment,
        promoCustomerSegmentDisplay,
        canonicalPromoEligibilityPreset,
        promoEligibilityFallbackMetadata,
        normalizePromoEligibilityMetadata,
        legacyPromoEligibilityRules,
        parsePromoEligibilityRules,
        inferPromoEligibilityPreset,
        promoEligibilityRulesCustomerIds,
        promoEligibilityConditionSummary,
        promoEligibilitySummary,
        serializePromoEligibilityRules,
        normalizePromoCustomerOption,
        filterPromoCustomerOptions,
        buildPromoSimulationIdentity,
        promoEligibilityDiagnosticRows,
        promoEligibilitySimulationConditions,
        promoSimulationDiagnosticRows,
        getPromoPayload,
        normalizedPromoFormStatus,
        setPromoOptionsForValidation(options) {
            promoOptions = options;
        },
        setPromoEligibilityRulesForValidation(prefix, rules, preset = "custom") {
            const parsed = parsePromoEligibilityRules(rules) || { version: 1, operator: "all", conditions: [] };
            const metadata = promoEligibilityMetadata() || promoEligibilityFallbackMetadata();
            promoEligibilityStates.set(prefix, {
                rules: withPromoEligibilityKeys(parsed),
                preset: canonicalPromoEligibilityPreset(preset),
                source: "explicit",
                legacySegment: "all",
                dirty: true,
                blockedReason: promoEligibilityUnsupportedReason(parsed, metadata),
                serverSummary: ""
            });
        },
        clearPromoEligibilityValidationState(prefix) {
            promoEligibilityStates.delete(prefix);
        }
    };
}

function normalizePromoActiveDays(values = [], strict = false) {
    const result = [];
    listValue(values).forEach((value, index) => {
        const key = String(value).trim().toLowerCase();
        const day = Object.prototype.hasOwnProperty.call(promoDayNameToNumber, key)
            ? promoDayNameToNumber[key]
            : (/^\d+$/.test(key) ? Number(key) : Number.NaN);
        if (Number.isInteger(day) && day >= 0 && day <= 6 && !result.includes(day)) {
            result.push(day);
        } else if (strict) {
            throw new Error(`Hari Aktif item ke-${index + 1} tidak valid.`);
        }
    });
    return result;
}

function detailToMessage(detail) {
    if (!detail) return "";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
        const messages = detail.map(formatValidationError);
        const uniqueMessages = [...new Set(messages)];
        if (uniqueMessages.length > 1) return `Periksa data berikut:\n- ${uniqueMessages.join("\n- ")}`;
        return uniqueMessages[0] || "";
    }
    if (typeof detail === "object") return detail.message || JSON.stringify(detail);
    return String(detail);
}

async function api(url, options = {}) {
    const headers = { ...(options.headers || {}), token };
    const res = await fetch(url, { ...options, headers });
    const raw = await res.text();
    let data = {};

    if (raw) {
        try {
            data = JSON.parse(raw);
        } catch (error) {
            data = { detail: raw };
        }
    }

    if (res.status === 401) {
        localStorage.removeItem("admin_token");
        localStorage.removeItem("admin_user");
        window.location.href = "/admin";
        throw new Error("Sesi admin habis. Silakan login ulang.");
    }

    if (!res.ok) {
        const requestError = new Error(detailToMessage(data.detail || data.error || data.message) || "Request gagal diproses.");
        requestError.status = res.status;
        requestError.data = data;
        throw requestError;
    }

    return data;
}

function showError(error, fallback) {
    console.error(fallback, error);
    alert(error.message || fallback || "Terjadi kesalahan.");
}

function hasPermission(permission) {
    return !!currentAdmin && Array.isArray(currentAdmin.permissions) && currentAdmin.permissions.includes(permission);
}

function ensurePermission(permission) {
    if (hasPermission(permission)) return true;
    showNoAccess();
    return false;
}

function setActiveMenu(menuId) {
    document.querySelectorAll(".sidebar .nav-link, .offcanvas .nav-link").forEach((el) => {
        el.classList.remove("active");
    });
    const active = menuId ? byId(menuId) : null;
    if (active) active.classList.add("active");
}

function hideWorkspace() {
    const globalSearchRow = document.querySelector(".admin-search-row");
    if (globalSearchRow) globalSearchRow.style.display = "";
    ["product_actions", "promo_actions", "admin_actions", "page_actions"].forEach((id) => {
        const node = byId(id);
        if (node) node.style.display = "none";
    });

    [
        "order_container",
        "product_container",
        "promo_container",
        "admin_container",
        "page_container",
        "settings_container",
        "api_monitor_container",
        "report_container",
        "customer_container",
        "audit_container"
    ].forEach((id) => {
        const node = byId(id);
        if (node) node.style.display = "none";
    });

    const categoryNav = byId("category_nav");
    if (categoryNav) categoryNav.style.display = "none";
}

function showNoAccess() {
    hideWorkspace();
    setActiveMenu(null);
    byId("page-title").innerText = "Akses Tidak Tersedia";
    byId("order_container").style.display = "block";
    byId("main_table").innerHTML = `
        <tbody>
            <tr><td class="text-center text-muted p-5">Akun admin ini belum punya akses ke fitur dashboard.</td></tr>
        </tbody>`;
}

function applyPermissionVisibility() {
    document.querySelectorAll("[data-permission]").forEach((node) => {
        const allowed = hasPermission(node.dataset.permission);
        node.style.display = allowed ? "" : "none";
    });
}

function formatCurrency(value) {
    return "Rp " + Number(value || 0).toLocaleString("id-ID");
}

function formatDate(value) {
    if (!value) return "-";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return escapeHtml(value);
    return date.toLocaleString("id-ID", { timeZone: APP_TIME_ZONE });
}

function paymentBadge(status) {
    const value = status || "-";
    if (value === "PAID") return "bg-success";
    if (["UNPAID", "PENDING"].includes(value)) return "bg-warning text-dark";
    if (["REFUNDED", "CANCELED", "EXPIRED", "FAILED"].includes(value)) return "bg-secondary";
    return "bg-light text-dark";
}

function topupBadge(status) {
    const value = status || "-";
    if (value === "SUCCESS") return "bg-success";
    if (value === "FAILED") return "bg-danger";
    if (value === "PENDING_PROVIDER") return "bg-warning text-dark";
    return "bg-info text-dark";
}

function toDateTimeInput(value) {
    if (!value) return "";
    const normalized = String(value).replace("Z", "");
    return normalized.slice(0, 16);
}

function slugify(value) {
    return String(value || "")
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
}

function permissionChips(permissions) {
    const values = permissions || [];
    if (!values.length) return `<span class="text-muted small">Tidak ada akses</span>`;
    return values.map((permission) => {
        const label = permissionLabels[permission] || permission;
        return `<span class="permission-chip">${escapeHtml(label)}</span>`;
    }).join("");
}

function renderPermissionChecks(containerId, selected = []) {
    const container = byId(containerId);
    if (!container) return;
    const selectedSet = new Set(selected);
    container.innerHTML = Object.entries(permissionLabels).map(([permission, label]) => {
        const fieldId = `${containerId}_${permission.replace(/[^a-z0-9]/gi, "_")}`;
        return `
            <div class="col-md-6 mb-2">
                <div class="form-check permission-chip">
                    <input class="form-check-input" type="checkbox" value="${escapeHtml(permission)}" id="${fieldId}" ${selectedSet.has(permission) ? "checked" : ""}>
                    <label class="form-check-label" for="${fieldId}">${escapeHtml(label)}</label>
                </div>
            </div>`;
    }).join("");
}

function collectPermissions(containerId) {
    const container = byId(containerId);
    if (!container) return [];
    return Array.from(container.querySelectorAll("input[type='checkbox']:checked")).map((item) => item.value);
}

function fillRoleSelect(selectId, selectedRole = "support") {
    const select = byId(selectId);
    if (!select) return;
    select.innerHTML = Object.keys(rolePermissions).map((role) => {
        return `<option value="${role}" ${role === selectedRole ? "selected" : ""}>${roleLabels[role] || role}</option>`;
    }).join("");
}

function bindRoleDefaults(selectId, containerId) {
    const select = byId(selectId);
    if (!select) return;
    select.addEventListener("change", () => {
        renderPermissionChecks(containerId, rolePermissions[select.value] || []);
    });
}

async function loadRoleMetadata() {
    if (!hasPermission("admins:manage")) return;
    const data = await api("/admin/api/roles");
    rolePermissions = data.roles || rolePermissions;
    permissionLabels = data.permissions || permissionLabels;
}

async function loadAdminContext() {
    currentAdmin = await api("/admin/api/me");
    permissionLabels = currentAdmin.permission_labels || permissionLabels;
    updateAdminIdentity();
    applyPermissionVisibility();
    if (hasPermission("admins:manage")) {
        await loadRoleMetadata();
    }
}

function updateAdminIdentity() {
    const siteName = currentSiteName || "LIXAFA PROJEK";
    const role = currentAdmin ? currentAdmin.role : "";
    const roleLabel = roleLabels[role] || (role ? role : "Admin");

    document.querySelectorAll("[data-site-name]").forEach((node) => {
        node.textContent = siteName;
    });

    document.querySelectorAll("[data-admin-role-label]").forEach((node) => {
        node.textContent = roleLabel;
    });
}

async function applyBranding() {
    try {
        const res = await fetch("/api/site-settings");
        if (!res.ok) return;
        const data = await res.json();
        const siteName = data.site_name || "LIXAFA PROJEK";
        currentSiteName = siteName;
        updateAdminIdentity();

        document.querySelectorAll("[data-brand-logo]").forEach((node) => {
            node.innerHTML = "";
            const logoUrl = safeMediaUrl(data.logo_url);
            if (logoUrl) {
                node.classList.add("has-image");
                const img = document.createElement("img");
                img.src = logoUrl;
                img.alt = siteName;
                node.appendChild(img);
            } else {
                node.classList.remove("has-image");
                node.textContent = "L";
            }
        });
    } catch (error) {
        console.error("Gagal memuat branding:", error);
    }
}

function loadFirstAllowedView() {
    const view = viewOrder.find((item) => hasPermission(item.permission));
    if (view) {
        view.loader();
        return;
    }
    showNoAccess();
}

window.loadStats = async function() {
    if (!hasPermission("orders:view")) return;
    try {
        const summary = await api("/admin/api/dashboard-summary");

        byId("revenue_today").innerText = formatCurrency(summary.today?.revenue || 0);
        byId("revenue_total").innerText = formatCurrency(summary.total?.revenue || 0);
        byId("profit_today").innerText = formatCurrency(summary.today?.realized_profit || 0);
        byId("profit_total").innerText = formatCurrency(summary.total?.realized_profit || 0);
        if (byId("waiting_payment")) byId("waiting_payment").innerText = Number(summary.operational?.waiting_payment || 0).toLocaleString("id-ID");
        if (byId("pending_provider")) byId("pending_provider").innerText = Number(summary.operational?.pending_provider || 0).toLocaleString("id-ID");
        if (byId("open_tickets")) byId("open_tickets").innerText = Number(summary.operational?.open_tickets || 0).toLocaleString("id-ID");
        if (byId("wallet_liability")) byId("wallet_liability").innerText = formatCurrency(summary.operational?.wallet_liability || 0);
    } catch (error) {
        console.error("Gagal load statistik:", error);
    }
};

window.loadOrders = async function() {
    if (!ensurePermission("orders:view")) return;
    hideWorkspace();
    setActiveMenu("nav-orders");
    byId("page-title").innerText = "Operasional Transaksi";
    byId("order_container").style.display = "block";
    byId("main_table").innerHTML = `<tbody><tr><td class="text-center p-4">Memuat transaksi...</td></tr></tbody>`;

    try {
        const params = new URLSearchParams();
        const search = (byId("searchInput")?.value || "").trim();
        const paymentStatus = byId("order_filter_payment")?.value || "";
        const topupStatus = byId("order_filter_topup")?.value || "";
        const dateFrom = byId("order_filter_from")?.value || "";
        const dateTo = byId("order_filter_to")?.value || "";
        const limit = byId("order_filter_limit")?.value || "150";
        if (search) params.set("q", search);
        if (paymentStatus) params.set("payment_status", paymentStatus);
        if (topupStatus) params.set("topup_status", topupStatus);
        if (dateFrom) params.set("date_from", dateFrom);
        if (dateTo) params.set("date_to", dateTo);
        params.set("limit", limit);
        const data = await api(`/admin/api/orders?${params.toString()}`);
        if (!Array.isArray(data) || data.length === 0) {
            byId("main_table").innerHTML = `<tbody><tr><td class="text-center text-muted p-4">Belum ada transaksi.</td></tr></tbody>`;
            return;
        }

        const canManage = hasPermission("orders:manage");
        const rows = data.map((order) => {
            const actionButtons = `
                <button class="btn btn-sm btn-outline-primary" onclick="showOrderDetail('${escapeJs(order.id)}')"><i class="bi bi-eye"></i></button>
                ${canManage && order.payment_status === "PAID" ? `<button class="btn btn-sm btn-outline-warning" onclick="retryOrder('${escapeJs(order.id)}')"><i class="bi bi-arrow-repeat"></i></button>` : ""}
                ${canManage && order.payment_status === "PAID" ? `<button class="btn btn-sm btn-outline-danger" onclick="refundOrder('${escapeJs(order.id)}')"><i class="bi bi-cash-coin"></i></button>` : ""}
            `;
            return `
                <tr>
                    <td><code>${escapeHtml(String(order.id || "").substring(0, 10))}</code></td>
                    <td>
                        <div class="fw-bold">${escapeHtml(order.phone || "-")}</div>
                        <small class="text-muted">${escapeHtml(order.target_id || "-")}</small>
                    </td>
                    <td>
                        <div class="fw-semibold">${escapeHtml(order.product_name || order.nominal || "-")}</div>
                        <small class="text-muted">${escapeHtml(order.nominal || "-")}</small>
                    </td>
                    <td>${formatCurrency(order.amount)}</td>
                    <td>${formatCurrency(order.realized_profit)}</td>
                    <td><span class="badge ${paymentBadge(order.payment_status)}">${escapeHtml(order.payment_status || "-")}</span></td>
                    <td><span class="badge ${topupBadge(order.topup_status)}">${escapeHtml(order.topup_status || "-")}</span></td>
                    <td><small class="text-muted">${formatDate(order.created_at)}</small></td>
                    <td class="text-end">
                        <div class="btn-group btn-group-sm">${actionButtons}</div>
                    </td>
                </tr>`;
        }).join("");

        byId("main_table").innerHTML = `
            <thead class="table-light">
                <tr><th>ID</th><th>Customer</th><th>Produk</th><th>Total</th><th>Profit</th><th>Pembayaran</th><th>Topup</th><th>Waktu</th><th class="text-end">Aksi</th></tr>
            </thead>
            <tbody>${rows}</tbody>`;
    } catch (error) {
        byId("main_table").innerHTML = `<tbody><tr><td class="text-danger text-center p-4">Gagal memuat transaksi.</td></tr></tbody>`;
        console.error(error);
    }
};

function renderOrderDetail(order) {
    const canManage = hasPermission("orders:manage");
    const detailRows = [
        ["Order ID", `<code>${escapeHtml(order.id)}</code>`],
        ["Customer", `${escapeHtml(order.phone || "-")}<br><small class="text-muted">Target: ${escapeHtml(order.target_id || "-")}</small>`],
        ["Produk", `${escapeHtml(order.product_name || order.nominal || "-")}<br><small class="text-muted">${escapeHtml(order.nominal || "-")}</small>`],
        ["Nickname", escapeHtml(order.nickname || "-")],
        ["Pembayaran", `<span class="badge ${paymentBadge(order.payment_status)}">${escapeHtml(order.payment_status || "-")}</span>`],
        ["Topup", `<span class="badge ${topupBadge(order.topup_status)}">${escapeHtml(order.topup_status || "-")}</span>`],
        ["Metode", escapeHtml(order.payment_method || "-")],
        ["Nama Payment", escapeHtml(order.payment_name || "-")],
        ["Reference Tripay", order.payment_reference ? `<code>${escapeHtml(order.payment_reference)}</code>` : "-"],
        ["Pay Code", order.pay_code ? `<code>${escapeHtml(order.pay_code)}</code>` : "-"],
        ["Tipe Order", escapeHtml(order.order_type || "PREPAID")],
        ["Total", formatCurrency(order.amount)],
        ["Harga Produk", formatCurrency(order.price)],
        ["Modal Produk", formatCurrency(order.product_cost)],
        ["Fee Payment", formatCurrency(order.payment_fee)],
        ["Profit Realisasi", formatCurrency(order.realized_profit)],
        ["Promo", escapeHtml(order.promo_code || "-")],
        ["SN", escapeHtml(order.sn || "-")],
        ["Catatan", escapeHtml(order.note || "-")],
        ["RC Provider", escapeHtml(order.provider_rc || "-")],
        ["Harga Provider", formatCurrency(order.provider_price || 0)],
        ["Saldo Provider", formatCurrency(order.provider_last_balance || 0)],
        ["Error Provider", escapeHtml(order.provider_last_error || "-")],
        ["Invoice", safeHref(order.invoice_url) ? `<a href="${escapeHtml(safeHref(order.invoice_url))}" target="_blank" rel="noopener">Buka invoice</a>` : "-"],
        ["Refund", order.refund_status ? `${escapeHtml(order.refund_status)}<br><small>${escapeHtml(order.refund_note || "")}</small>` : "-"],
        ["Dibuat", formatDate(order.created_at)],
        ["Update Terakhir", formatDate(order.status_updated_at)]
    ];

    const managePanel = canManage ? `
        <hr>
        <input type="hidden" id="detail_order_id" value="${escapeHtml(order.id)}">
        <div class="row">
            <div class="col-md-6 mb-2">
                <label class="form-label fw-bold">Payment Status</label>
                <select id="detail_payment_status" class="form-select">
                    ${["UNPAID", "PAID", "EXPIRED", "CANCELED", "FAILED", "REFUNDED"].map((item) => `<option value="${item}" ${order.payment_status === item ? "selected" : ""}>${item}</option>`).join("")}
                </select>
            </div>
            <div class="col-md-6 mb-2">
                <label class="form-label fw-bold">Topup Status</label>
                <select id="detail_topup_status" class="form-select">
                    ${["PENDING_PAYMENT", "PROCESSING", "PENDING_PROVIDER", "SUCCESS", "FAILED"].map((item) => `<option value="${item}" ${order.topup_status === item ? "selected" : ""}>${item}</option>`).join("")}
                </select>
            </div>
            <div class="col-md-6 mb-2">
                <label class="form-label fw-bold">SN</label>
                <input id="detail_sn" class="form-control" value="${escapeHtml(order.sn || "")}">
            </div>
            <div class="col-md-6 mb-2">
                <label class="form-label fw-bold">Catatan</label>
                <input id="detail_note" class="form-control" value="${escapeHtml(order.note || "")}">
            </div>
        </div>
        <div class="d-flex flex-wrap gap-2 justify-content-end">
            <button class="btn btn-outline-primary" onclick="syncOrderTripay('${escapeJs(order.id)}')"><i class="bi bi-credit-card"></i> Sync Tripay</button>
            <button class="btn btn-outline-info" onclick="syncOrderProvider('${escapeJs(order.id)}')"><i class="bi bi-lightning-charge"></i> Sync Provider</button>
            <button class="btn btn-outline-warning" onclick="retryOrder('${escapeJs(order.id)}')"><i class="bi bi-arrow-repeat"></i> Retry</button>
            <button class="btn btn-outline-danger" onclick="refundOrder('${escapeJs(order.id)}')"><i class="bi bi-cash-coin"></i> Refund</button>
            <button class="btn btn-primary" onclick="saveOrderStatus()"><i class="bi bi-save"></i> Simpan Status</button>
        </div>` : "";

    return `
        <div class="table-responsive">
            <table class="table table-sm align-middle">
                <tbody>
                    ${detailRows.map(([label, value]) => `<tr><th style="width:180px">${label}</th><td>${value}</td></tr>`).join("")}
                </tbody>
            </table>
        </div>
        ${managePanel}`;
}

window.showOrderDetail = async function(orderId) {
    if (!ensurePermission("orders:view")) return;
    const content = byId("order_detail_content");
    content.innerHTML = `<div class="text-center p-4"><div class="spinner-border text-warning"></div><br>Memuat detail order...</div>`;
    const modal = getModal("orderDetailModal");
    modal?.show();

    try {
        const order = await api(`/admin/api/orders/${encodeURIComponent(orderId)}`);
        content.innerHTML = renderOrderDetail(order);
    } catch (error) {
        content.innerHTML = `<div class="alert alert-danger">Gagal memuat detail order.</div>`;
        showError(error, "Gagal memuat detail order.");
    }
};

window.retryOrder = async function(orderId) {
    if (!ensurePermission("orders:manage")) return;
    if (!confirm("Retry order ini ke provider?")) return;
    try {
        await api(`/admin/api/orders/${encodeURIComponent(orderId)}/retry`, { method: "POST" });
        await window.loadOrders();
        await window.loadStats();
        await window.showOrderDetail(orderId);
    } catch (error) {
        showError(error, "Gagal retry order.");
    }
};

window.syncOrderTripay = async function(orderId) {
    if (!ensurePermission("orders:manage")) return;
    try {
        await api(`/admin/api/orders/${encodeURIComponent(orderId)}/sync-tripay`, { method: "POST" });
        await showOrderDetail(orderId);
        await window.loadOrders();
        alert("Status Tripay berhasil disinkronkan.");
    } catch (error) {
        showError(error, "Gagal sync Tripay.");
    }
};

window.syncOrderProvider = async function(orderId) {
    if (!ensurePermission("orders:manage")) return;
    try {
        await api(`/admin/api/orders/${encodeURIComponent(orderId)}/sync-provider`, { method: "POST" });
        await showOrderDetail(orderId);
        await window.loadOrders();
        alert("Status provider berhasil disinkronkan.");
    } catch (error) {
        showError(error, "Gagal sync provider.");
    }
};

window.refundOrder = async function(orderId) {
    if (!ensurePermission("orders:manage")) return;
    const note = prompt("Catatan refund:", "Refund manual dari dashboard admin");
    if (note === null) return;
    try {
        await api(`/admin/api/orders/${encodeURIComponent(orderId)}/refund`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ note })
        });
        await window.loadOrders();
        await window.loadStats();
        await window.showOrderDetail(orderId);
    } catch (error) {
        showError(error, "Gagal refund order.");
    }
};

window.saveOrderStatus = async function() {
    if (!ensurePermission("orders:manage")) return;
    const orderId = byId("detail_order_id")?.value;
    if (!orderId) return;
    try {
        await api(`/admin/api/orders/${encodeURIComponent(orderId)}/status`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                payment_status: byId("detail_payment_status").value,
                topup_status: byId("detail_topup_status").value,
                sn: byId("detail_sn").value,
                note: byId("detail_note").value
            })
        });
        await window.loadOrders();
        await window.loadStats();
        await window.showOrderDetail(orderId);
    } catch (error) {
        showError(error, "Gagal menyimpan status order.");
    }
};

window.showBulkMarkupModal = async function() {
    if (!ensurePermission("products:manage")) return;
    const selectElement = byId("bulk_brand");
    selectElement.innerHTML = `<option value="">Mengecek database...</option>`;

    const modalElement = byId("bulkMarkupModal");
    const modalInstance = bootstrap.Modal.getInstance(modalElement) || new bootstrap.Modal(modalElement);
    modalInstance.show();

    try {
        if (!Object.keys(allGroupedProducts).length) {
            allGroupedProducts = await api("/admin/api/products");
        }

        const providers = new Set();
        Object.values(allGroupedProducts).forEach((providerMap) => {
            Object.keys(providerMap || {}).forEach((provider) => providers.add(provider));
        });

        const providerOptions = Array.from(providers).sort().map((provider) => {
            return `<option value="${escapeHtml(provider.toUpperCase())}">${escapeHtml(provider)}</option>`;
        }).join("");

        selectElement.innerHTML = `<option value="ALL">SEMUA PRODUK (ALL)</option>${providerOptions}`;
    } catch (error) {
        console.error("Gagal bikin list provider:", error);
        selectElement.innerHTML = `<option value="">Gagal memuat provider</option>`;
    }
};

window.saveBulkMarkup = async function() {
    if (!ensurePermission("products:manage")) return;
    const brand = byId("bulk_brand").value;
    const percent = parseFloat(byId("bulk_percent").value);
    const minProfit = parseInt(byId("bulk_min_profit").value, 10) || 0;

    if (!brand) {
        alert("Pilih provider dulu.");
        return;
    }
    if (Number.isNaN(percent) || percent <= 0) {
        alert("Masukkan persentase profit yang valid.");
        return;
    }

    const btn = document.querySelector("#bulkMarkupModal .btn-warning");
    const originalText = btn.innerHTML;
    btn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Memproses...`;
    btn.disabled = true;

    try {
        const res = await api("/admin/bulk-markup", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ brand, percent, min_profit: minProfit })
        });
        alert(res.message || "Harga berhasil diperbarui.");
        bootstrap.Modal.getInstance(byId("bulkMarkupModal"))?.hide();
        window.loadProducts();
    } catch (error) {
        showError(error, "Gagal update harga massal.");
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
};

window.loadProducts = async function() {
    if (!ensurePermission("products:manage")) return;
    hideWorkspace();
    setActiveMenu("nav-products");
    byId("page-title").innerText = "Manajemen Produk";
    byId("product_actions").style.display = "block";
    byId("category_nav").style.display = "block";
    byId("product_container").style.display = "block";
    byId("product_container").innerHTML = `<div class="text-center p-5"><div class="spinner-border text-warning"></div><br>Memuat produk...</div>`;

    try {
        allGroupedProducts = await api("/admin/api/products");
        const categories = Object.keys(allGroupedProducts);
        const navContainer = document.querySelector("#category_nav ul");
        navContainer.innerHTML = "";

        if (!categories.length) {
            byId("product_container").innerHTML = `<div class="alert alert-info">Belum ada produk di database.</div>`;
            return;
        }

        categories.forEach((category, index) => {
            const li = document.createElement("li");
            li.className = "nav-item";
            const button = document.createElement("button");
            button.className = `nav-link ${index === 0 ? "active" : ""}`;
            button.textContent = category;
            button.addEventListener("click", () => window.switchCategory(category, button));
            li.appendChild(button);
            navContainer.appendChild(li);
        });

        renderByKategori(categories[0]);
    } catch (error) {
        byId("product_container").innerHTML = `<div class="alert alert-danger">Gagal memuat produk.</div>`;
        console.error(error);
    }
};

window.switchCategory = function(category, button) {
    document.querySelectorAll("#category_nav .nav-link").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    renderByKategori(category);
};

function findProductBySku(sku) {
    for (const providerMap of Object.values(allGroupedProducts || {})) {
        for (const products of Object.values(providerMap || {})) {
            const found = (products || []).find((product) => String(product.sku) === String(sku));
            if (found) return found;
        }
    }
    return null;
}

function renderByKategori(category) {
    const container = byId("product_container");
    container.innerHTML = "";
    const providers = allGroupedProducts[category] || {};

    if (!Object.keys(providers).length) {
        container.innerHTML = `<div class="alert alert-info">Belum ada produk di kategori ${escapeHtml(category)}.</div>`;
        return;
    }

    Object.entries(providers).forEach(([providerName, products]) => {
        const rows = products.map((product) => {
            const statusBadge = product.active ? `<span class="badge bg-success">Aktif</span>` : `<span class="badge bg-secondary">Mati</span>`;
            const mediaBadge = product.logo_url || product.image_url ? `<span class="badge bg-info text-dark">Media</span>` : "";
            const promoBadge = product.promo_badge ? `<span class="badge bg-warning text-dark">${escapeHtml(product.promo_badge)}</span>` : "";
            return `
                <tr>
                    <td>
                        <code>${escapeHtml(product.sku)}</code>
                        <div class="small text-muted">Urutan ${Number(product.display_order || 0).toLocaleString("id-ID")}</div>
                    </td>
                    <td>
                        <div class="fw-bold">${escapeHtml(product.name)}</div>
                        <div class="small text-muted">${escapeHtml(product.description || product.promo_text || "-")}</div>
                    </td>
                    <td class="small">${formatCurrency(product.cost)}</td>
                    <td class="text-primary fw-bold">${formatCurrency(product.price)}</td>
                    <td class="text-success small">${formatCurrency(product.profit)}</td>
                    <td>
                        ${statusBadge} ${mediaBadge} ${promoBadge}
                        <div class="small text-muted mt-1">${Number(product.order_count || 0).toLocaleString("id-ID")} order / ${Number(product.success_count || 0).toLocaleString("id-ID")} sukses</div>
                    </td>
                    <td class="text-center">
                        <button class="btn btn-sm btn-outline-primary" onclick="showEditModal('${escapeJs(product.sku)}')"><i class="bi bi-pencil-square"></i></button>
                        <button class="btn btn-sm ${product.active ? "btn-outline-warning" : "btn-outline-success"}" onclick="toggleProduct('${escapeJs(product.sku)}')"><i class="bi bi-power"></i></button>
                        <button class="btn btn-sm btn-outline-danger" onclick="deleteProduct('${escapeJs(product.sku)}')"><i class="bi bi-trash"></i></button>
                    </td>
                </tr>`;
        }).join("");

        container.innerHTML += `
            <div class="card border-0 shadow-sm mb-4 rounded-3 overflow-hidden">
                <div class="card-header bg-dark text-white fw-bold d-flex justify-content-between align-items-center p-3">
                    <span><i class="bi bi-tag-fill me-2 text-warning"></i> ${escapeHtml(providerName.toUpperCase())}</span>
                    <span class="badge bg-secondary">${products.length} Item</span>
                </div>
                <div class="table-responsive">
                    <table class="table table-hover align-middle mb-0">
                        <thead class="table-light small">
                            <tr><th>SKU</th><th>Item</th><th>Modal</th><th>Jual</th><th>Profit</th><th>Status & Performa</th><th class="text-center">Aksi</th></tr>
                        </thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
            </div>`;
    });
}

window.showAddProductModal = function() {
    if (!ensurePermission("products:manage")) return;
    [
        "add_provider", "add_name", "add_sku", "add_cost", "add_price", "add_category",
        "add_description", "add_logo_url", "add_image_url", "add_promo_badge",
        "add_promo_title", "add_promo_text", "add_promo_url"
    ].forEach((id) => {
        byId(id).value = "";
    });
    byId("add_display_order").value = 0;
    addModal.show();
};

window.saveNewProduct = async function() {
    if (!ensurePermission("products:manage")) return;
    const payload = {
        provider: byId("add_provider").value,
        name: byId("add_name").value,
        sku: byId("add_sku").value,
        cost: parseInt(byId("add_cost").value, 10),
        price: parseInt(byId("add_price").value, 10),
        category: byId("add_category").value || "Lainnya",
        description: byId("add_description").value,
        logo_url: byId("add_logo_url").value,
        image_url: byId("add_image_url").value,
        promo_badge: byId("add_promo_badge").value,
        promo_title: byId("add_promo_title").value,
        promo_text: byId("add_promo_text").value,
        promo_url: byId("add_promo_url").value,
        display_order: parseInt(byId("add_display_order").value, 10) || 0
    };

    try {
        await api("/admin/api/products", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        addModal.hide();
        window.loadProducts();
    } catch (error) {
        showError(error, "Gagal menyimpan produk.");
    }
};

window.showEditModal = function(sku) {
    if (!ensurePermission("products:manage")) return;
    const product = findProductBySku(sku);
    if (!product) {
        alert("Produk tidak ditemukan di cache dashboard. Silakan refresh halaman produk.");
        return;
    }
    byId("edit_id").value = sku;
    byId("edit_provider").value = product.provider || "";
    byId("edit_name").value = product.name || "";
    byId("edit_cost").value = product.cost || 0;
    byId("edit_price").value = product.price || 0;
    byId("edit_category").value = product.category || "";
    byId("edit_display_order").value = product.display_order || 0;
    byId("edit_description").value = product.description || "";
    byId("edit_logo_url").value = product.logo_url || "";
    byId("edit_image_url").value = product.image_url || "";
    byId("edit_product_promo_badge").value = product.promo_badge || "";
    byId("edit_product_promo_title").value = product.promo_title || "";
    byId("edit_promo_text").value = product.promo_text || "";
    byId("edit_promo_url").value = product.promo_url || "";
    editModal.show();
};

window.saveEditProduct = async function() {
    if (!ensurePermission("products:manage")) return;
    const sku = byId("edit_id").value;
    const payload = {
        provider: byId("edit_provider").value,
        name: byId("edit_name").value,
        cost: parseInt(byId("edit_cost").value, 10),
        price: parseInt(byId("edit_price").value, 10),
        category: byId("edit_category").value || "Lainnya",
        description: byId("edit_description").value,
        logo_url: byId("edit_logo_url").value,
        image_url: byId("edit_image_url").value,
        promo_badge: byId("edit_product_promo_badge").value,
        promo_title: byId("edit_product_promo_title").value,
        promo_text: byId("edit_promo_text").value,
        promo_url: byId("edit_promo_url").value,
        display_order: parseInt(byId("edit_display_order").value, 10) || 0
    };

    try {
        await api(`/admin/api/products/${encodeURIComponent(sku)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        editModal.hide();
        window.loadProducts();
    } catch (error) {
        showError(error, "Gagal memperbarui produk.");
    }
};

window.toggleProduct = async function(sku) {
    if (!ensurePermission("products:manage")) return;
    try {
        await api(`/admin/api/products/${encodeURIComponent(sku)}/toggle`, { method: "PUT" });
        window.loadProducts();
    } catch (error) {
        showError(error, "Gagal mengubah status produk.");
    }
};

window.deleteProduct = async function(sku) {
    if (!ensurePermission("products:manage")) return;
    if (!confirm(`Hapus permanen SKU: ${sku}?`)) return;
    try {
        await api(`/admin/api/products/${encodeURIComponent(sku)}`, { method: "DELETE" });
        window.loadProducts();
    } catch (error) {
        showError(error, "Gagal menghapus produk.");
    }
};

const PROMO_WIZARD_STEPS = [
    "Informasi Dasar",
    "Jenis & Nilai",
    "Target Promo",
    "Syarat & Batas",
    "Jadwal",
    "Tampilan Website",
    "Simulasi & Review"
];

function promoWizardExtraMarkup(prefix, step) {
    if (step === 1) {
        return `
            <div class="col-md-4"><label class="form-label" for="${prefix}_internal_code">Kode internal</label><input id="${prefix}_internal_code" class="form-control" autocomplete="off" placeholder="ID internal, tidak tampil ke pelanggan"><div class="form-text">Berbeda dari kode voucher pelanggan.</div></div>
            <div class="col-md-4"><label class="form-label" for="${prefix}_status">Status lifecycle</label><select id="${prefix}_status" class="form-select"><option value="draft">Draft</option><option value="active">Aktif</option><option value="paused">Dijeda</option><option value="ended">Diakhiri</option><option value="disabled">Dinonaktifkan</option><option value="archived">Diarsipkan</option></select><div class="form-text">Promo dengan waktu mulai di masa depan otomatis berstatus Dijadwalkan saat runtime.</div></div>
            <div class="col-12"><label class="form-label" for="${prefix}_internal_description">Deskripsi internal</label><textarea id="${prefix}_internal_description" class="form-control" rows="2" placeholder="Tujuan bisnis dan catatan singkat untuk tim"></textarea></div>
            <div class="col-12"><div class="d-flex align-items-center justify-content-between gap-2"><label class="form-label" for="${prefix}_customer_description">Deskripsi pelanggan</label><button type="button" class="btn btn-sm btn-outline-primary" data-promo-autofill>Isi otomatis</button></div><textarea id="${prefix}_customer_description" class="form-control" rows="3" placeholder="Benefit dan syarat singkat yang aman ditampilkan"></textarea></div>
            <div class="col-12"><label class="form-label" for="${prefix}_admin_notes">Catatan admin</label><textarea id="${prefix}_admin_notes" class="form-control" rows="2" placeholder="Tidak ditampilkan ke pelanggan"></textarea></div>`;
    }
    if (step === 2) {
        return `
            <div class="col-md-6"><label class="form-label" for="${prefix}_promo_type">Jenis promo terstruktur</label><select id="${prefix}_promo_type" class="form-select"><option value="banner">Konten banner</option><option value="automatic">Diskon otomatis</option><option value="voucher">Kode voucher</option><option value="special_price">Harga khusus produk</option><option value="payment_method">Promo metode pembayaran</option></select></div>
            <div class="col-md-6" data-promo-pricing><label class="form-label" for="${prefix}_minimum_transaction">Minimum transaksi</label><input type="number" min="0" step="1" id="${prefix}_minimum_transaction" class="form-control" placeholder="0 = tanpa minimum"></div>
            <div class="col-md-6" data-promo-special-price><label class="form-label" for="${prefix}_special_price">Harga khusus</label><input type="number" min="0" step="1" id="${prefix}_special_price" class="form-control" placeholder="Harga jual setelah promo"></div>
            <div class="col-md-6" data-promo-pricing><label class="form-label" for="${prefix}_rounding_rule">Pembulatan</label><select id="${prefix}_rounding_rule" class="form-select"><option value="none">Tanpa pembulatan</option><option value="floor_100">Turun ke Rp100</option><option value="ceil_100">Naik ke Rp100</option><option value="floor_500">Turun ke Rp500</option><option value="ceil_500">Naik ke Rp500</option><option value="floor_1000">Turun ke Rp1.000</option><option value="ceil_1000">Naik ke Rp1.000</option></select></div>`;
    }
    if (step === 3) {
        return `
            <div class="col-md-6" data-promo-target-selector><label class="form-label" for="${prefix}_target_values">Target tambahan</label><select id="${prefix}_target_values" class="form-select" multiple size="6" aria-describedby="${prefix}_target_values_help"></select><div class="form-text" id="${prefix}_target_values_help">Ctrl/Cmd + klik untuk memilih lebih dari satu. Target utama legacy tetap dipertahankan.</div></div>
            <div class="col-md-6" data-promo-exclusion-selector><label class="form-label" for="${prefix}_target_exclusions">Pengecualian produk/SKU</label><select id="${prefix}_target_exclusions" class="form-select" multiple size="6"></select><div class="form-text">Produk yang dipilih tidak menerima promo.</div></div>
            <div class="col-12"><div id="${prefix}_target_impact" class="promo-inline-summary" aria-live="polite">Pilih target untuk melihat estimasi dampak.</div></div>`;
    }
    if (step === 4) {
        return `
            <div class="col-md-4"><label class="form-label" for="${prefix}_quota_daily">Kuota per hari</label><input type="number" min="0" step="1" id="${prefix}_quota_daily" class="form-control" placeholder="0 = tanpa batas"></div>
            <div class="col-md-4"><label class="form-label" for="${prefix}_max_per_customer_daily">Limit akun per hari</label><input type="number" min="0" step="1" id="${prefix}_max_per_customer_daily" class="form-control" placeholder="0 = tanpa batas"></div>
            <div class="col-md-4" data-promo-legacy-segment><label class="form-label" for="${prefix}_customer_segment">Target pelanggan</label><select id="${prefix}_customer_segment" class="form-select" aria-describedby="${prefix}_customer_segment_help">${promoCustomerSegmentOptionMarkup()}</select><div class="form-text" id="${prefix}_customer_segment_help">Promo dapat digunakan oleh guest maupun member yang login.</div></div>
            <div class="col-12 promo-eligibility-builder" data-promo-eligibility-builder hidden>
                <div class="promo-eligibility-toolbar">
                    <div><label class="form-label" for="${prefix}_eligibility_preset">Preset pelanggan</label><select id="${prefix}_eligibility_preset" class="form-select">${promoEligibilityPresetOptionMarkup()}</select><div class="form-text" id="${prefix}_eligibility_preset_help">Preset mengisi daftar syarat dan tetap dapat diperiksa atau disesuaikan.</div></div>
                    <div><label class="form-label" for="${prefix}_eligibility_operator">Penuhi</label><select id="${prefix}_eligibility_operator" class="form-select"><option value="all">SEMUA syarat (DAN)</option><option value="any">SALAH SATU syarat (ATAU)</option></select><div class="form-text">Satu grup aturan tanpa nested condition.</div></div>
                </div>
                <div id="${prefix}_eligibility_legacy_notice" class="alert alert-info py-2 mt-3 mb-0" hidden>Aturan legacy yang dikonversi untuk tampilan. Promo live belum berubah; aturan baru baru disimpan saat form ini disubmit.</div>
                <div id="${prefix}_eligibility_blocked_notice" class="alert alert-danger py-2 mt-3 mb-0" hidden></div>
                <div class="d-flex justify-content-between align-items-center gap-2 mt-3"><strong class="small">Daftar kondisi</strong><button type="button" class="btn btn-sm btn-outline-primary" id="${prefix}_eligibility_add" data-promo-eligibility-add>Tambah Syarat</button></div>
                <div id="${prefix}_eligibility_conditions" class="promo-eligibility-conditions mt-2" aria-live="polite"></div>
                <div id="${prefix}_eligibility_summary" class="promo-inline-summary mt-3" aria-live="polite">Promo berlaku untuk semua pelanggan.</div>
            </div>
            <div class="col-md-8" data-promo-customer-target hidden>
                <label class="form-label" for="${prefix}_customer_search">Cari pelanggan terdaftar</label>
                <input type="search" id="${prefix}_customer_search" class="form-control" autocomplete="off" placeholder="Cari nama, nomor WhatsApp, atau ID akun" aria-describedby="${prefix}_customer_search_help">
                <input type="hidden" id="${prefix}_customer_ids" value="">
                <div class="form-text" id="${prefix}_customer_search_help">Pilih satu atau beberapa akun aktif dari hasil pencarian. ID disimpan secara canonical.</div>
                <div id="${prefix}_customer_search_results" class="list-group mt-2" role="listbox" aria-label="Hasil pencarian pelanggan"></div>
                <div class="d-flex justify-content-between align-items-center gap-2 mt-3"><strong class="small">Pelanggan terpilih</strong><span class="badge bg-secondary" id="${prefix}_customer_selected_count">0 pelanggan dipilih</span></div>
                <div id="${prefix}_customer_selected" class="d-flex flex-wrap gap-2 mt-2" aria-live="polite"></div>
            </div>
            <div class="col-md-4"><label class="form-label" for="${prefix}_exclusive">Eksklusif</label><select id="${prefix}_exclusive" class="form-select"><option value="0">Tidak</option><option value="1">Ya, tidak dapat digabung</option></select></div>
            <div class="col-md-4"><label class="form-label" for="${prefix}_max_promotions_per_order">Maks promo per order</label><input type="number" min="1" step="1" id="${prefix}_max_promotions_per_order" class="form-control" value="1"></div>`;
    }
    if (step === 5) {
        return `
            <div class="col-md-4"><label class="form-label" for="${prefix}_timezone">Zona waktu</label><select id="${prefix}_timezone" class="form-select"><option value="Asia/Jakarta">Asia/Jakarta (WIB)</option><option value="Asia/Makassar">Asia/Makassar (WITA)</option><option value="Asia/Jayapura">Asia/Jayapura (WIT)</option><option value="UTC">UTC</option></select></div>
            <div class="col-md-4"><label class="form-label" for="${prefix}_daily_start">Jam aktif mulai</label><input type="time" id="${prefix}_daily_start" class="form-control"></div>
            <div class="col-md-4"><label class="form-label" for="${prefix}_daily_end">Jam aktif berakhir</label><input type="time" id="${prefix}_daily_end" class="form-control"></div>
            <fieldset class="col-12"><legend class="form-label">Hari aktif</legend><div id="${prefix}_active_days" class="promo-check-grid">${[
                { value: 1, label: "Sen" },
                { value: 2, label: "Sel" },
                { value: 3, label: "Rab" },
                { value: 4, label: "Kam" },
                { value: 5, label: "Jum" },
                { value: 6, label: "Sab" },
                { value: 0, label: "Min" }
            ].map((day) => `<label class="form-check"><input class="form-check-input" type="checkbox" value="${day.value}" checked><span class="form-check-label">${day.label}</span></label>`).join("")}</div></fieldset>`;
    }
    if (step === 6) {
        return `
            <div class="col-md-4"><label class="form-label" for="${prefix}_display_order">Urutan tampil</label><input type="number" min="0" step="1" id="${prefix}_display_order" class="form-control" value="0"></div>
            <div class="col-md-8"><div class="form-check promo-external-cta-check"><input class="form-check-input" type="checkbox" id="${prefix}_allow_external_cta"><label class="form-check-label" for="${prefix}_allow_external_cta">Izinkan CTA menuju website eksternal</label><div class="form-text">URL eksternal akan diberi peringatan dan dibuka aman di tab baru.</div></div></div>
            <fieldset class="col-12"><legend class="form-label">Penempatan</legend><div id="${prefix}_placements" class="promo-check-grid"><label class="form-check"><input class="form-check-input" type="checkbox" value="home_banner"><span class="form-check-label">Banner utama</span></label><label class="form-check"><input class="form-check-input" type="checkbox" value="promo_cards" checked><span class="form-check-label">Kartu promo</span></label><label class="form-check"><input class="form-check-input" type="checkbox" value="product"><span class="form-check-label">Halaman produk</span></label><label class="form-check"><input class="form-check-input" type="checkbox" value="checkout"><span class="form-check-label">Checkout</span></label></div></fieldset>`;
    }
    if (step === 7) {
        return `
            <div class="col-12"><h6 class="promo-section-subtitle">Preview pelanggan</h6><div id="${prefix}_customer_preview" class="promo-customer-preview" aria-live="polite"></div></div>
            <div class="col-12"><h6 class="promo-section-subtitle">Ringkasan sebelum disimpan</h6><div id="${prefix}_review" class="promo-review-panel" aria-live="polite"></div></div>`;
    }
    return "";
}

function promoFieldBlock(input, body) {
    if (!input) return null;
    if (input.id.endsWith("_simulation_result")) return input.closest(".border.rounded-3") || input.parentElement;
    const column = input.closest('[class*="col-md-"], [class*="col-lg-"], [class*="col-xl-"], .col-12');
    if (column && (!body || body.contains(column))) return column;
    const check = input.closest(".form-check");
    if (check && (!body || body.contains(check))) return check;
    const spaced = input.closest(".mb-3");
    if (spaced && (!body || body.contains(spaced))) return spaced;
    return input;
}

function initializePromoWizard(prefix, modalId, saveHandler) {
    const modal = byId(modalId);
    const body = modal?.querySelector(".modal-body");
    const footer = modal?.querySelector(".modal-footer");
    if (!body || body.dataset.wizardReady === "1") return;
    body.dataset.wizardReady = "1";
    body.classList.add("promo-wizard-body");

    const nav = document.createElement("div");
    nav.className = "promo-wizard-nav";
    nav.setAttribute("role", "tablist");
    nav.setAttribute("aria-label", "Tahapan form promo");
    nav.innerHTML = PROMO_WIZARD_STEPS.map((label, index) => `<button type="button" class="promo-step-button${index === 0 ? " active" : ""}" data-promo-step-target="${index + 1}" role="tab" aria-selected="${index === 0 ? "true" : "false"}"><span>${index + 1}</span>${escapeHtml(label)}</button>`).join("");

    const feedback = document.createElement("div");
    feedback.id = `${prefix}_form_feedback`;
    feedback.className = "promo-form-feedback";
    feedback.setAttribute("aria-live", "assertive");

    const content = document.createElement("div");
    content.className = "promo-wizard-content";
    const panes = {};
    PROMO_WIZARD_STEPS.forEach((label, index) => {
        const step = index + 1;
        const pane = document.createElement("section");
        pane.className = `promo-step-pane${step === 1 ? " active" : ""}`;
        pane.dataset.promoStep = String(step);
        pane.setAttribute("role", "tabpanel");
        pane.hidden = step !== 1;
        pane.innerHTML = `<div class="promo-section-heading"><span>Tahap ${step}</span><h6>${escapeHtml(label)}</h6></div><div class="row g-3 promo-step-grid"></div>`;
        panes[step] = pane;
        content.appendChild(pane);
    });

    body.prepend(content);
    body.prepend(feedback);
    body.prepend(nav);

    const fieldsByStep = {
        1: ["title", "code", "badge", "description"],
        2: ["rule_type", "discount_type", "discount_value", "max_discount"],
        3: ["target_scope", "target_value", "payment_method_search"],
        4: ["usage_mode", "usage_limit", "budget_limit", "max_per_customer", "max_per_phone", "max_per_target", "stackable", "priority"],
        5: ["starts_at", "ends_at"],
        6: ["cta_preset", "cta_text", "cta_url", "image_url", "show_on_website", "active"],
        7: ["simulation_result"]
    };
    const moved = new Set();
    Object.entries(fieldsByStep).forEach(([step, suffixes]) => {
        suffixes.forEach((suffix) => {
            const input = byId(`${prefix}_${suffix}`);
            const block = promoFieldBlock(input, body);
            if (!block || moved.has(block) || block.closest(".promo-step-pane")) return;
            moved.add(block);
            panes[step].querySelector(".promo-step-grid").appendChild(block);
        });
    });

    const hiddenId = byId(`${prefix}_id`);
    if (hiddenId) panes[1].appendChild(hiddenId);
    Object.entries(panes).forEach(([step, pane]) => {
        pane.querySelector(".promo-step-grid").insertAdjacentHTML("beforeend", promoWizardExtraMarkup(prefix, Number(step)));
    });

    Array.from(body.children).forEach((child) => {
        if ([nav, feedback, content].includes(child)) return;
        if (child.matches(".row") && !child.children.length) child.remove();
        else panes[7].querySelector(".promo-step-grid").appendChild(child);
    });

    const legacyDescription = byId(`${prefix}_description`);
    if (legacyDescription) {
        const block = promoFieldBlock(legacyDescription, body);
        if (block) block.classList.add("promo-legacy-description");
        const customerDescription = byId(`${prefix}_customer_description`);
        customerDescription?.addEventListener("input", () => {
            legacyDescription.value = customerDescription.value;
        });
    }

    body.querySelectorAll("input, select, textarea").forEach((field) => {
        const localLabel = field.closest('[class*="col-"], .mb-3, .form-check')?.querySelector("label:not([for])");
        if (field.id && localLabel) localLabel.setAttribute("for", field.id);
    });

    if (footer) {
        footer.classList.add("promo-wizard-footer");
        footer.innerHTML = `
            <button type="button" class="btn btn-outline-secondary" data-promo-wizard-cancel data-bs-dismiss="modal">Batal</button>
            <div class="promo-wizard-footer-status"><span id="${prefix}_step_label">Tahap 1 dari 7</span></div>
            <button type="button" class="btn btn-outline-primary" data-promo-step-prev disabled>Sebelumnya</button>
            <button type="button" class="btn btn-primary" data-promo-step-next>Berikutnya</button>
            <button type="button" class="btn btn-primary" data-promo-save hidden>Simpan Promo</button>`;
        footer.querySelector("[data-promo-step-prev]")?.addEventListener("click", () => setPromoWizardStep(prefix, Number(body.dataset.currentStep || 1) - 1));
        footer.querySelector("[data-promo-step-next]")?.addEventListener("click", () => setPromoWizardStep(prefix, Number(body.dataset.currentStep || 1) + 1));
        footer.querySelector("[data-promo-save]")?.addEventListener("click", saveHandler);
    }
    nav.querySelectorAll("[data-promo-step-target]").forEach((button) => {
        button.addEventListener("click", () => setPromoWizardStep(prefix, Number(button.dataset.promoStepTarget || 1)));
    });
    body.querySelector("[data-promo-autofill]")?.addEventListener("click", () => window.autofillPromoDescription(prefix));
    bindPromoCustomerSelector(prefix);
    bindPromoEligibilityBuilder(prefix);
    bindPromoSimulationIdentity(prefix);

    ["promo_type", "rule_type", "discount_type", "target_scope", "usage_mode", "customer_segment", "status", "cta_url", "allow_external_cta"].forEach((suffix) => {
        byId(`${prefix}_${suffix}`)?.addEventListener("change", () => updatePromoConditionalFields(prefix));
        byId(`${prefix}_${suffix}`)?.addEventListener("input", () => updatePromoConditionalFields(prefix));
    });
    byId(`${prefix}_active`)?.addEventListener("change", (event) => {
        const statusField = byId(`${prefix}_status`);
        if (!statusField) return;
        if (event.target.checked && ["draft", "paused", "disabled", "ended"].includes(statusField.value)) statusField.value = "active";
        if (!event.target.checked && statusField.value === "active") statusField.value = "disabled";
        updatePromoConditionalFields(prefix);
    });
    body.addEventListener("input", () => renderPromoReview(prefix));
    body.addEventListener("change", () => renderPromoReview(prefix));
    setPromoWizardStep(prefix, 1);
    updatePromoConditionalFields(prefix);
}

function setPromoWizardStep(prefix, requestedStep) {
    const modal = byId(prefix === "promo" ? "addPromoModal" : "editPromoModal");
    const body = modal?.querySelector(".modal-body");
    if (!body) return;
    const step = Math.max(1, Math.min(PROMO_WIZARD_STEPS.length, Number(requestedStep || 1)));
    body.dataset.currentStep = String(step);
    body.querySelectorAll("[data-promo-step]").forEach((pane) => {
        const active = Number(pane.dataset.promoStep) === step;
        pane.hidden = !active;
        pane.classList.toggle("active", active);
    });
    body.querySelectorAll("[data-promo-step-target]").forEach((button) => {
        const active = Number(button.dataset.promoStepTarget) === step;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", active ? "true" : "false");
    });
    const footer = modal.querySelector(".modal-footer");
    const prev = footer?.querySelector("[data-promo-step-prev]");
    const next = footer?.querySelector("[data-promo-step-next]");
    const save = footer?.querySelector("[data-promo-save]");
    if (prev) prev.disabled = step === 1;
    if (next) next.hidden = step === PROMO_WIZARD_STEPS.length;
    if (save) save.hidden = step !== PROMO_WIZARD_STEPS.length;
    const label = byId(`${prefix}_step_label`);
    if (label) label.textContent = `Tahap ${step} dari ${PROMO_WIZARD_STEPS.length}: ${PROMO_WIZARD_STEPS[step - 1]}`;
    if (step === PROMO_WIZARD_STEPS.length) renderPromoReview(prefix);
}

function canonicalPromoType(value) {
    const normalized = String(value || "").trim().toLowerCase();
    if (normalized === "content") return "banner";
    if (normalized === "payment") return "payment_method";
    return ["banner", "automatic", "voucher", "special_price", "payment_method"].includes(normalized)
        ? normalized
        : "";
}

function inferPromoType(promo = {}, prefix = "promo") {
    const explicit = canonicalPromoType(promo.promo_type || promo.promotion_type);
    if (explicit) return explicit;
    if ((promo.rule_type || byId(`${prefix}_rule_type`)?.value) === "content") return "banner";
    if (promo.special_price) return "special_price";
    if ((promo.payment_methods || []).length) return "payment_method";
    if (promo.code || byId(`${prefix}_code`)?.value) return "voucher";
    return "automatic";
}

function updatePromoConditionalFields(prefix) {
    const promoType = canonicalPromoType(byId(`${prefix}_promo_type`)?.value) || inferPromoType({}, prefix);
    const ruleTypeField = byId(`${prefix}_rule_type`);
    if (ruleTypeField) {
        ruleTypeField.value = promoType === "banner" ? "content" : "price";
    }
    const ruleType = ruleTypeField?.value || "content";
    const discountType = byId(`${prefix}_discount_type`)?.value || "";
    const pricingEnabled = promoType !== "banner" || ruleType === "price";
    const body = byId(prefix === "promo" ? "addPromoModal" : "editPromoModal")?.querySelector(".modal-body");
    body?.querySelectorAll("[data-promo-pricing]").forEach((node) => node.hidden = !pricingEnabled);
    body?.querySelectorAll("[data-promo-special-price]").forEach((node) => node.hidden = promoType !== "special_price");
    if (!pricingEnabled) {
        if (byId(`${prefix}_discount_type`)) byId(`${prefix}_discount_type`).value = "";
        if (byId(`${prefix}_discount_value`)) byId(`${prefix}_discount_value`).value = "";
        if (byId(`${prefix}_max_discount`)) byId(`${prefix}_max_discount`).value = "";
        if (byId(`${prefix}_special_price`)) byId(`${prefix}_special_price`).value = "";
    } else if (!discountType || promoType === "special_price") {
        if (byId(`${prefix}_discount_value`)) byId(`${prefix}_discount_value`).value = "";
        if (byId(`${prefix}_max_discount`)) byId(`${prefix}_max_discount`).value = "";
    } else if (discountType !== "percent" && byId(`${prefix}_max_discount`)) {
        byId(`${prefix}_max_discount`).value = "";
    }
    const maxDiscount = byId(`${prefix}_max_discount`);
    if (maxDiscount) {
        const block = promoFieldBlock(maxDiscount, body);
        if (block) block.hidden = !pricingEnabled || discountType !== "percent";
    }
    ["discount_type", "discount_value"].forEach((suffix) => {
        const node = byId(`${prefix}_${suffix}`);
        const block = promoFieldBlock(node, body);
        if (block) block.hidden = !pricingEnabled || promoType === "special_price";
    });
    const scope = byId(`${prefix}_target_scope`)?.value || "all";
    body?.querySelectorAll("[data-promo-target-selector]").forEach((node) => node.hidden = scope === "all");
    const targetValue = byId(`${prefix}_target_value`);
    const targetBlock = promoFieldBlock(targetValue, body);
    if (targetBlock) targetBlock.hidden = scope === "all";
    if (scope === "all") {
        if (targetValue) targetValue.value = "";
        const advancedTargets = byId(`${prefix}_target_values`);
        if (advancedTargets) Array.from(advancedTargets.options).forEach((option) => { option.selected = false; });
    }
    const usageMode = byId(`${prefix}_usage_mode`)?.value || "unlimited";
    const usageLimit = byId(`${prefix}_usage_limit`);
    if (usageLimit) {
        usageLimit.disabled = usageMode !== "limited";
        if (usageMode !== "limited") usageLimit.value = "";
    }
    const status = byId(`${prefix}_status`)?.value || "draft";
    const activeInput = byId(`${prefix}_active`);
    if (activeInput && document.activeElement === byId(`${prefix}_status`)) {
        activeInput.checked = status === "active";
    }
    const customerSegment = byId(`${prefix}_customer_segment`)?.value || "all";
    const eligibilityEnabled = !!promoEligibilityMetadata();
    const customerTargetRequired = eligibilityEnabled
        ? promoEligibilityNeedsCustomerSelector(prefix)
        : customerSegment === "specific";
    body?.querySelectorAll("[data-promo-customer-target]").forEach((node) => { node.hidden = !customerTargetRequired; });
    if (!customerTargetRequired && (promoCustomerSelection(prefix).size || byId(`${prefix}_customer_ids`)?.value)) {
        clearPromoCustomerSelections(prefix);
    } else if (customerTargetRequired) {
        syncPromoCustomerSelection(prefix);
    }
    const customerSegmentHelp = byId(`${prefix}_customer_segment_help`);
    if (customerSegmentHelp) customerSegmentHelp.textContent = promoCustomerSegmentMeta(customerSegment).description;
    const cta = validatePromoCtaUrl(byId(`${prefix}_cta_url`)?.value, !!byId(`${prefix}_allow_external_cta`)?.checked);
    const feedback = byId(`${prefix}_form_feedback`);
    if (feedback) {
        feedback.textContent = cta.valid ? "" : cta.reason;
        feedback.classList.toggle("show", !cta.valid);
    }
    renderPromoTargetImpact(prefix);
    renderPromoReview(prefix);
}

function renderPromoTargetImpact(prefix) {
    const output = byId(`${prefix}_target_impact`);
    if (!output) return;
    const scope = byId(`${prefix}_target_scope`)?.value || "all";
    const selected = selectedMultiValues(`${prefix}_target_values`);
    const excluded = selectedMultiValues(`${prefix}_target_exclusions`);
    const totalProducts = (promoOptions?.skus || []).length;
    if (scope === "all") output.textContent = `Semua ${totalProducts.toLocaleString("id-ID")} produk katalog berpotensi terdampak; ${excluded.length} pengecualian dipilih.`;
    else output.textContent = `${selected.length || (byId(`${prefix}_target_value`)?.value ? 1 : 0)} target ${scope} dipilih; ${excluded.length} produk/SKU dikecualikan.`;
}

function renderPromoReview(prefix) {
    const review = byId(`${prefix}_review`);
    const preview = byId(`${prefix}_customer_preview`);
    if (!review && !preview) return;
    const title = byId(`${prefix}_title`)?.value || "Promo belum diberi nama";
    const badge = byId(`${prefix}_badge`)?.value || "Promo";
    const customerDescription = byId(`${prefix}_customer_description`)?.value || byId(`${prefix}_description`)?.value || "Deskripsi promo akan tampil di sini.";
    const code = byId(`${prefix}_code`)?.value || "";
    const scope = byId(`${prefix}_target_scope`)?.value || "all";
    const target = selectedMultiLabels(`${prefix}_target_values`).join(", ") || byId(`${prefix}_target_value`)?.selectedOptions?.[0]?.textContent?.trim() || "Semua produk";
    const methods = selectedMultiValues(`${prefix}_payment_methods`);
    const customerSegmentValue = byId(`${prefix}_customer_segment`)?.value || "all";
    const eligibilityState = promoEligibilityStates.get(prefix);
    const customerEligibility = promoEligibilityMetadata() && eligibilityState
        ? (eligibilityState.blockedReason && eligibilityState.serverSummary
            ? eligibilityState.serverSummary
            : promoEligibilitySummary(eligibilityState.rules, eligibilityState.preset))
        : promoCustomerSegmentDisplay(customerSegmentValue, promoCustomerIds(prefix).length);
    const status = byId(`${prefix}_status`)?.value || "draft";
    const ctaText = byId(`${prefix}_cta_text`)?.value || "Lihat Promo";
    const imageUrl = safeMediaUrl(byId(`${prefix}_image_url`)?.value || "");
    if (preview) {
        preview.innerHTML = `${imageUrl ? `<img class="promo-preview-image" src="${escapeHtml(imageUrl)}" alt="Preview ${escapeHtml(title)}">` : ""}<div class="promo-preview-badge">${escapeHtml(badge)}</div><h5>${escapeHtml(title)}</h5><p>${escapeHtml(customerDescription)}</p>${code ? `<div class="promo-preview-code">Kode: <strong>${escapeHtml(code)}</strong></div>` : ""}<button type="button" class="btn btn-sm btn-warning" disabled>${escapeHtml(ctaText)}</button>`;
    }
    if (review) {
        review.innerHTML = `<dl><div><dt>Status</dt><dd>${escapeHtml(status)}</dd></div><div><dt>Jenis</dt><dd>${escapeHtml(byId(`${prefix}_promo_type`)?.value || "content")}</dd></div><div><dt>Target</dt><dd>${escapeHtml(scope)} - ${escapeHtml(target)}</dd></div><div><dt>Syarat pelanggan</dt><dd>${escapeHtml(customerEligibility)}</dd></div><div><dt>Pembayaran</dt><dd>${escapeHtml(methods.length ? methods.join(", ") : "Semua metode")}</dd></div><div><dt>Diskon</dt><dd>${escapeHtml(promoDiscountText(prefix))}</dd></div><div><dt>Periode</dt><dd>${escapeHtml(byId(`${prefix}_starts_at`)?.value || "langsung")} s.d. ${escapeHtml(byId(`${prefix}_ends_at`)?.value || "tanpa batas")}</dd></div></dl>`;
    }
}

function canonicalPromoCustomerSegment(value) {
    const cleaned = String(value || "all").trim().toLowerCase();
    const aliases = { all_customers: "all", member: "members_only", guest: "guests_only" };
    return aliases[cleaned] || cleaned || "all";
}

function promoCustomerSegmentRows() {
    const configured = Array.isArray(promoOptions?.customer_segments) ? promoOptions.customer_segments : [];
    const rows = new Map(PROMO_CUSTOMER_SEGMENT_DEFAULTS.map((item) => [item.value, { ...item }]));
    configured.forEach((item) => {
        const value = canonicalPromoCustomerSegment(item?.value);
        if (!value) return;
        const localPresentation = rows.get(value);
        if (!localPresentation) return;
        rows.set(value, { ...item, ...localPresentation, value, aliases: [...new Set([...(item?.aliases || []), ...(localPresentation.aliases || [])])] });
    });
    return Array.from(rows.values());
}

function promoCustomerSegmentMeta(value) {
    const canonical = canonicalPromoCustomerSegment(value);
    return promoCustomerSegmentRows().find((item) => item.value === canonical) || {
        value: canonical,
        label: "Segmentasi lama (perlu diperiksa)",
        description: "Nilai segmentasi lama dipertahankan agar promo tetap dapat diperiksa."
    };
}

function promoCustomerSegmentOptionMarkup(selectedValue = "all") {
    const selected = canonicalPromoCustomerSegment(selectedValue);
    const rows = promoCustomerSegmentRows();
    if (selected && !rows.some((item) => item.value === selected)) rows.push(promoCustomerSegmentMeta(selected));
    return rows.map((item) => `<option value="${escapeHtml(item.value)}" title="${escapeHtml(item.description || "")}"${item.value === selected ? " selected" : ""}>${escapeHtml(item.label || item.value)}</option>`).join("");
}

function populatePromoCustomerSegments(prefix, selectedValue = "all") {
    const select = byId(`${prefix}_customer_segment`);
    if (!select) return;
    const selected = canonicalPromoCustomerSegment(selectedValue);
    select.innerHTML = promoCustomerSegmentOptionMarkup(selected);
    select.value = selected;
}

function promoCustomerSegmentDisplay(value, selectedCount = 0) {
    const meta = promoCustomerSegmentMeta(value);
    const parsedCount = Number(selectedCount || 0);
    const count = Number.isFinite(parsedCount) ? Math.max(0, parsedCount) : 0;
    return meta.value === "specific"
        ? `${meta.label}: ${count.toLocaleString("id-ID")} pelanggan dipilih`
        : meta.label;
}

function clonePromoEligibilityValue(value) {
    if (Array.isArray(value)) return value.map((item) => clonePromoEligibilityValue(item));
    if (value && typeof value === "object") {
        return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, clonePromoEligibilityValue(item)]));
    }
    return value;
}

function canonicalPromoEligibilityPreset(value) {
    const normalized = String(value || "all").trim().toLowerCase();
    const aliases = { new: "first_purchase", first_buyer: "first_purchase", members: "members_only", guests: "guests_only" };
    return aliases[normalized] || normalized || "all";
}

function normalizePromoEligibilityOption(item) {
    const source = item && typeof item === "object" ? item : { value: item, label: item };
    const value = source.value ?? source.id ?? source.code;
    if (value === null || value === undefined || String(value).trim() === "") return null;
    return { value, label: String(source.label ?? source.name ?? value) };
}

function normalizePromoEligibilityFieldType(value) {
    const normalized = String(value || "string").trim().toLowerCase();
    if (["int", "integer", "number", "count", "days"].includes(normalized)) return "integer";
    if (["money", "currency", "rupiah", "decimal"].includes(normalized)) return "money";
    if (["date", "datetime", "timestamp"].includes(normalized)) return "datetime";
    if (["bool", "boolean"].includes(normalized)) return "boolean";
    if (["select", "choice", "enum"].includes(normalized)) return "enum";
    if (["customer", "customer_id", "customer_ids"].includes(normalized)) return "customer";
    return "string";
}

function normalizePromoEligibilityMetadata(raw) {
    if (!raw || typeof raw !== "object") return null;
    const fields = listValue(raw.fields).map((item) => {
        if (!item || typeof item !== "object") return null;
        const value = String(item.value ?? item.field ?? item.name ?? "").trim();
        const safeField = PROMO_ELIGIBILITY_FALLBACK.fields.find((field) => field.value === value);
        if (!safeField) return null;
        const operators = listValue(item.operators ?? item.allowed_operators)
            .map((operator) => String(operator?.value ?? operator).trim().toLowerCase())
            .filter((operator) => safeField.operators.includes(operator));
        if (!operators.length) return null;
        const configuredOptions = listValue(item.options ?? item.choices).map(normalizePromoEligibilityOption).filter(Boolean);
        const safeOptions = listValue(safeField.options);
        const options = safeOptions.length
            ? (configuredOptions.length ? configuredOptions : safeOptions)
                .filter((option) => safeOptions.some((safeOption) => String(safeOption.value) === String(option.value)))
                .map((option) => ({
                    value: option.value,
                    label: String(safeOptions.find((safeOption) => String(safeOption.value) === String(option.value))?.label || option.label)
                }))
            : [];
        return {
            value,
            label: String(item.label || safeField.label),
            type: normalizePromoEligibilityFieldType(safeField.type),
            operators,
            options,
            min: item.min ?? item.minimum ?? safeField.min ?? null,
            max: item.max ?? item.maximum ?? safeField.max ?? null,
            unit: String(item.unit || safeField.unit || "")
        };
    }).filter(Boolean);
    const groupOperators = listValue(raw.group_operators ?? raw.operators).map((item) => {
        const value = String(item?.value ?? item).trim().toLowerCase();
        if (!["all", "any"].includes(value)) return null;
        return { value, label: String(item?.label || (value === "any" ? "Penuhi SALAH SATU syarat" : "Penuhi SEMUA syarat")) };
    }).filter(Boolean);
    return {
        version: Number(raw.version || 1),
        max_conditions: Math.max(1, Number(raw.max_conditions || raw.max_rules || 20)),
        max_bytes: Math.max(256, Number(raw.max_bytes || 16384)),
        group_operators: groupOperators.length ? groupOperators : clonePromoEligibilityValue(PROMO_ELIGIBILITY_FALLBACK.group_operators),
        fields,
        presets: listValue(raw.presets)
    };
}

function promoEligibilityMetadata() {
    return normalizePromoEligibilityMetadata(promoOptions?.eligibility);
}

function promoEligibilityFallbackMetadata() {
    return normalizePromoEligibilityMetadata(PROMO_ELIGIBILITY_FALLBACK);
}

function promoEligibilityFieldRows(metadata = promoEligibilityMetadata() || promoEligibilityFallbackMetadata()) {
    return metadata?.fields || [];
}

function promoEligibilityFieldMeta(field, metadata = promoEligibilityMetadata() || promoEligibilityFallbackMetadata()) {
    const canonical = String(field || "").trim();
    return promoEligibilityFieldRows(metadata).find((item) => item.value === canonical) || null;
}

function promoEligibilityPresetRows(metadata = promoEligibilityMetadata() || promoEligibilityFallbackMetadata()) {
    const presentation = new Map(PROMO_ELIGIBILITY_FALLBACK.presets.map((item) => [item.value, clonePromoEligibilityValue(item)]));
    listValue(metadata?.presets).forEach((item) => {
        if (!item || typeof item !== "object") return;
        const value = canonicalPromoEligibilityPreset(item.value ?? item.id ?? item.name);
        const local = presentation.get(value) || { value, label: humanizeField(value), description: "" };
        const rules = item.rules ?? item.eligibility_rules ?? local.rules;
        presentation.set(value, {
            ...local,
            ...item,
            value,
            label: String(local.label || item.label || value),
            description: String(local.description || item.description || ""),
            rules: rules ? clonePromoEligibilityValue(rules) : null
        });
    });
    const ordered = PROMO_ELIGIBILITY_FALLBACK.presets.map((item) => item.value);
    return Array.from(presentation.values()).sort((first, second) => {
        const firstIndex = ordered.indexOf(first.value);
        const secondIndex = ordered.indexOf(second.value);
        return (firstIndex < 0 ? 999 : firstIndex) - (secondIndex < 0 ? 999 : secondIndex);
    });
}

function promoEligibilityPresetOptionMarkup(selectedValue = "all") {
    const selected = canonicalPromoEligibilityPreset(selectedValue);
    return promoEligibilityPresetRows().map((item) => `<option value="${escapeHtml(item.value)}" title="${escapeHtml(item.description || "")}"${item.value === selected ? " selected" : ""}>${escapeHtml(item.label || item.value)}</option>`).join("");
}

function legacyPresetFromCustomerSegment(segment) {
    const canonical = canonicalPromoCustomerSegment(segment);
    return canonical === "new" ? "first_purchase" : (["all", "members_only", "guests_only", "existing", "specific"].includes(canonical) ? canonical : "custom");
}

function legacyCustomerSegmentFromPreset(preset) {
    const canonical = canonicalPromoEligibilityPreset(preset);
    if (canonical === "first_purchase") return "new";
    if (["all", "members_only", "guests_only", "existing", "specific"].includes(canonical)) return canonical;
    return "all";
}

function legacyPromoEligibilityRules(segment, customerIds = []) {
    const preset = legacyPresetFromCustomerSegment(segment);
    const row = promoEligibilityPresetRows(promoEligibilityFallbackMetadata()).find((item) => item.value === preset);
    const rules = clonePromoEligibilityValue(row?.rules || { version: 1, operator: "all", conditions: [] });
    if (preset === "specific") {
        const ids = [...new Set(listValue(customerIds).map((value) => Number(value?.id ?? value)).filter((value) => Number.isInteger(value) && value > 0))];
        rules.conditions = [{ field: "customer_id", operator: "in", value: ids }];
    }
    return rules;
}

function parsePromoEligibilityRules(raw) {
    if (raw === null || raw === undefined || raw === "") return null;
    let source = raw;
    if (typeof source === "string") {
        try {
            source = JSON.parse(source);
        } catch (_) {
            return { version: 0, operator: "all", conditions: [], invalid_json: true };
        }
    }
    if (!source || typeof source !== "object" || Array.isArray(source)) return { version: 0, operator: "all", conditions: [], invalid_json: true };
    return {
        version: Number(source.version || 1),
        operator: String(source.operator || "all").trim().toLowerCase(),
        conditions: listValue(source.conditions).map((item) => ({
            field: String(item?.field || "").trim(),
            operator: String(item?.operator || "").trim().toLowerCase(),
            value: clonePromoEligibilityValue(item?.value)
        })),
        preset: source.preset ? canonicalPromoEligibilityPreset(source.preset) : "",
        invalid_json: false
    };
}

function promoEligibilityRulesCustomerIds(raw) {
    const rules = parsePromoEligibilityRules(raw);
    if (!rules) return [];
    return [...new Set(rules.conditions
        .filter((condition) => condition.field === "customer_id")
        .flatMap((condition) => listValue(condition.value))
        .map(Number)
        .filter((value) => Number.isInteger(value) && value > 0))];
}

function promoEligibilityHasCustomerCondition(raw) {
    return !!parsePromoEligibilityRules(raw)?.conditions?.some((condition) => condition.field === "customer_id");
}

function nextPromoEligibilityConditionKey() {
    promoEligibilityConditionSequence += 1;
    return `condition-${promoEligibilityConditionSequence}`;
}

function withPromoEligibilityKeys(rules) {
    return {
        version: Number(rules?.version || 1),
        operator: String(rules?.operator || "all").toLowerCase(),
        conditions: listValue(rules?.conditions).map((condition) => ({ ...clonePromoEligibilityValue(condition), _key: nextPromoEligibilityConditionKey() }))
    };
}

function promoEligibilityUnsupportedReason(rules, metadata, extraReason = "") {
    if (extraReason) return extraReason;
    if (!metadata) return "";
    if (rules?.invalid_json) return "JSON aturan eligibility tidak valid. Aturan tidak diubah agar promo tetap fail-closed.";
    if (Number(rules?.version || 0) !== 1 || Number(metadata.version || 0) !== 1) return "Versi aturan eligibility belum didukung dashboard ini.";
    const groupOperators = new Set(metadata.group_operators.map((item) => item.value));
    if (!groupOperators.has(String(rules.operator || ""))) return "Operator grup aturan tidak dikenali.";
    if (rules.conditions.length > metadata.max_conditions) return `Jumlah kondisi melebihi batas ${metadata.max_conditions}.`;
    for (const condition of rules.conditions) {
        const field = promoEligibilityFieldMeta(condition.field, metadata);
        if (!field) return "Terdapat field aturan yang belum didukung dashboard ini.";
        if (!field.operators.includes(condition.operator)) return `Operator untuk ${field.label} tidak didukung.`;
    }
    return "";
}

function promoEligibilityRulesEqual(first, second) {
    const clean = (rules) => ({
        version: Number(rules?.version || 1),
        operator: String(rules?.operator || "all"),
        conditions: listValue(rules?.conditions).map((condition) => ({ field: condition.field, operator: condition.operator, value: condition.value }))
    });
    return JSON.stringify(clean(first)) === JSON.stringify(clean(second));
}

function inferPromoEligibilityPreset(rules, source = "explicit", legacySegment = "all") {
    if (source === "legacy_adapter") return legacyPresetFromCustomerSegment(legacySegment);
    const parsed = parsePromoEligibilityRules(rules) || { version: 1, operator: "all", conditions: [] };
    if (parsed.preset && promoEligibilityPresetRows().some((item) => item.value === parsed.preset)) return parsed.preset;
    for (const preset of promoEligibilityPresetRows()) {
        if (!preset.rules || preset.value === "custom" || preset.value === "member_new" || preset.value === "specific") continue;
        if (promoEligibilityRulesEqual(parsed, preset.rules)) return preset.value;
    }
    const fields = parsed.conditions.map((item) => `${item.field}:${item.operator}`);
    if (parsed.operator === "all" && fields.includes("customer_id:in") && parsed.conditions.length === 1) return "specific";
    const authentication = parsed.conditions.find((item) => item.field === "authentication_status" && item.operator === "equals");
    const account = parsed.conditions.find((item) => item.field === "account_status" && item.operator === "equals");
    const age = parsed.conditions.find((item) => item.field === "account_age_days" && item.operator === "less_than_or_equal");
    const memberNewExtras = parsed.conditions.filter((item) => ![authentication, account, age].includes(item));
    const optionalFirstPurchase = memberNewExtras.length === 1
        && memberNewExtras[0].field === "successful_order_count"
        && memberNewExtras[0].operator === "equals"
        && Number(memberNewExtras[0].value) === 0;
    if (parsed.operator === "all"
        && authentication?.value === "member"
        && account?.value === "active"
        && age
        && (!memberNewExtras.length || optionalFirstPurchase)) return "member_new";
    return "custom";
}

function promoEligibilityState(prefix) {
    if (!promoEligibilityStates.has(prefix)) {
        promoEligibilityStates.set(prefix, {
            rules: withPromoEligibilityKeys({ version: 1, operator: "all", conditions: [] }),
            preset: "all",
            source: "explicit",
            legacySegment: "all",
            dirty: false,
            blockedReason: "",
            serverSummary: ""
        });
    }
    return promoEligibilityStates.get(prefix);
}

function promoEligibilityValueLabel(field, value) {
    if (value && typeof value === "object" && !Array.isArray(value) && Number.isInteger(Number(value.protected_value_count))) {
        const count = Math.max(0, Number(value.protected_value_count));
        return field?.type === "customer" ? `${count.toLocaleString("id-ID")} akun dipilih` : `${count.toLocaleString("id-ID")} nilai terlindungi`;
    }
    const values = listValue(value);
    if (field?.type === "customer") return `${values.length.toLocaleString("id-ID")} akun dipilih`;
    if (["normalized_phone", "target_id"].includes(field?.value)) return `${values.length.toLocaleString("id-ID")} identitas terlindungi`;
    if (field?.type === "money") {
        const number = Number(values[0] ?? value);
        return Number.isFinite(number) ? `Rp${Math.max(0, number).toLocaleString("id-ID")}` : "nilai belum valid";
    }
    if (field?.type === "boolean") {
        const boolean = value === true || value === 1 || value === "1" || String(value).toLowerCase() === "true";
        return boolean ? "Ya" : "Tidak";
    }
    const optionLabels = new Map(listValue(field?.options).map((option) => [String(option.value), option.label]));
    const formatted = values.map((item) => field?.type === "enum"
        ? (optionLabels.get(String(item)) || "Pilihan tidak dikenali")
        : String(item));
    if (formatted.length > 1) return formatted.join(", ");
    const scalar = formatted[0] ?? String(value ?? "");
    return field?.unit && scalar ? `${scalar} ${field.unit}` : (scalar || "nilai belum diisi");
}

function promoEligibilityConditionSummary(condition, metadata = promoEligibilityMetadata() || promoEligibilityFallbackMetadata()) {
    const field = promoEligibilityFieldMeta(condition?.field, metadata);
    if (!field) return "Kondisi tidak dikenali oleh dashboard ini";
    const operator = String(condition?.operator || "");
    if (operator === "is_true") return `${field.label}: Ya`;
    if (operator === "is_false") return `${field.label}: Tidak`;
    const operatorLabel = PROMO_ELIGIBILITY_OPERATOR_LABELS[operator] || "memenuhi aturan";
    if (operator === "between") {
        const range = listValue(condition?.value);
        return `${field.label} ${operatorLabel.toLowerCase()} ${promoEligibilityValueLabel(field, range[0])} dan ${promoEligibilityValueLabel(field, range[1])}`;
    }
    return `${field.label} ${operatorLabel.toLowerCase()} ${promoEligibilityValueLabel(field, condition?.value)}`;
}

function promoEligibilitySummary(rules, preset = "custom", metadata = promoEligibilityMetadata() || promoEligibilityFallbackMetadata()) {
    const parsed = parsePromoEligibilityRules(rules) || { version: 1, operator: "all", conditions: [] };
    const canonicalPreset = canonicalPromoEligibilityPreset(preset);
    if (!parsed.conditions.length) return "Promo berlaku untuk semua pelanggan.";
    if (canonicalPreset === "members_only") return "Promo berlaku untuk member yang sudah masuk ke akun aktif.";
    if (canonicalPreset === "guests_only") return "Promo berlaku untuk checkout tanpa login akun.";
    if (canonicalPreset === "first_purchase") return "Guest atau member yang belum pernah memiliki transaksi berhasil dapat menggunakan promo ini.";
    if (canonicalPreset === "existing") return "Promo berlaku untuk guest atau member yang sudah pernah memiliki minimal satu transaksi berhasil.";
    if (canonicalPreset === "specific") return `Promo berlaku untuk ${promoEligibilityRulesCustomerIds(parsed).length.toLocaleString("id-ID")} akun customer yang dipilih.`;
    if (canonicalPreset === "member_new") {
        const age = parsed.conditions.find((condition) => condition.field === "account_age_days")?.value;
        const firstPurchase = parsed.conditions.some((condition) => condition.field === "successful_order_count" && condition.operator === "equals" && Number(condition.value) === 0);
        return `Promo berlaku untuk member aktif dengan umur akun maksimal ${Number(age || 0).toLocaleString("id-ID")} hari${firstPurchase ? " dan belum pernah memiliki transaksi berhasil" : ""}.`;
    }
    const connector = parsed.operator === "any" ? " ATAU " : " DAN ";
    return `Promo berlaku jika ${parsed.operator === "any" ? "salah satu" : "semua"} syarat terpenuhi: ${parsed.conditions.map((condition) => promoEligibilityConditionSummary(condition, metadata)).join(connector)}.`;
}

function promoEligibilitySummaryFromPromo(promo = {}) {
    const parsed = parsePromoEligibilityRules(promo.eligibility_rules);
    const source = String(promo.eligibility_source || (parsed ? "explicit" : "legacy_adapter")).toLowerCase();
    if (parsed && ["explicit", "legacy_adapter"].includes(source)) {
        const metadata = promoEligibilityMetadata() || promoEligibilityFallbackMetadata();
        const unsupported = promoEligibilityUnsupportedReason(parsed, metadata);
        if (!unsupported) return promoEligibilitySummary(parsed, inferPromoEligibilityPreset(parsed, source, promo.eligibility_legacy_segment || promo.customer_segment), metadata);
    }
    if (promo.eligibility_summary) return String(promo.eligibility_summary);
    return promoCustomerSegmentDisplay(promo.customer_segment, listValue(promo.customer_ids).length);
}

function defaultPromoEligibilityValue(field, operator) {
    if (["is_true", "is_false"].includes(operator)) return null;
    if (operator === "between") return field.type === "datetime" ? ["", ""] : [field.min ?? 0, field.min ?? 0];
    if (["in", "not_in"].includes(operator)) return [];
    if (field.type === "enum") return field.options[0]?.value ?? "";
    if (field.type === "boolean") return true;
    if (["integer", "money"].includes(field.type)) return field.min ?? 0;
    return "";
}

function createPromoEligibilityCondition(fieldMeta) {
    const field = fieldMeta || promoEligibilityFieldRows()[0];
    if (!field) return null;
    const operator = field.operators[0] || "equals";
    return { field: field.value, operator, value: defaultPromoEligibilityValue(field, operator), _key: nextPromoEligibilityConditionKey() };
}

function promoEligibilityFieldOptionMarkup(selectedField, metadata) {
    return promoEligibilityFieldRows(metadata).map((field) => `<option value="${escapeHtml(field.value)}"${field.value === selectedField ? " selected" : ""}>${escapeHtml(field.label)}</option>`).join("");
}

function promoEligibilityOperatorOptionMarkup(field, selectedOperator) {
    return listValue(field?.operators).map((operator) => `<option value="${escapeHtml(operator)}"${operator === selectedOperator ? " selected" : ""}>${escapeHtml(PROMO_ELIGIBILITY_OPERATOR_LABELS[operator] || humanizeField(operator))}</option>`).join("");
}

function promoEligibilityScalarInputMarkup(prefix, condition, field, part = "") {
    const suffix = part ? `_${part}` : "";
    const id = `${prefix}_eligibility_${condition._key}_value${suffix}`;
    const rawValue = part ? listValue(condition.value)[part === "min" ? 0 : 1] : condition.value;
    const dataAttribute = part ? `data-promo-eligibility-value-${part}` : "data-promo-eligibility-value";
    if (field.type === "enum") {
        const selected = new Set(listValue(rawValue).map(String));
        const multiple = ["in", "not_in"].includes(condition.operator);
        return `<select id="${escapeHtml(id)}" class="form-select" ${dataAttribute}${multiple ? " multiple size=\"3\"" : ""}>${field.options.map((option) => `<option value="${escapeHtml(option.value)}"${selected.has(String(option.value)) ? " selected" : ""}>${escapeHtml(option.label)}</option>`).join("")}</select>`;
    }
    if (field.type === "boolean") {
        const selected = rawValue === false || rawValue === 0 || rawValue === "0" || String(rawValue).toLowerCase() === "false" ? "false" : "true";
        return `<select id="${escapeHtml(id)}" class="form-select" ${dataAttribute}><option value="true"${selected === "true" ? " selected" : ""}>Ya</option><option value="false"${selected === "false" ? " selected" : ""}>Tidak</option></select>`;
    }
    if (["integer", "money"].includes(field.type)) {
        const min = field.min !== null && field.min !== undefined ? ` min="${escapeHtml(String(field.min))}"` : "";
        const max = field.max !== null && field.max !== undefined ? ` max="${escapeHtml(String(field.max))}"` : "";
        return `<input id="${escapeHtml(id)}" type="number" step="1"${min}${max} class="form-control" value="${escapeHtml(String(rawValue ?? ""))}" ${dataAttribute} placeholder="${field.type === "money" ? "Nominal Rupiah" : "Angka"}">`;
    }
    if (field.type === "datetime") {
        return `<input id="${escapeHtml(id)}" type="datetime-local" class="form-control" value="${escapeHtml(toDateTimeInput(rawValue))}" ${dataAttribute}>`;
    }
    const listOperator = ["in", "not_in"].includes(condition.operator);
    const displayValue = listOperator ? listValue(rawValue).join(", ") : String(rawValue ?? "");
    return `<input id="${escapeHtml(id)}" type="text" class="form-control" value="${escapeHtml(displayValue)}" ${dataAttribute} autocomplete="off" placeholder="${listOperator ? "Pisahkan beberapa nilai dengan koma" : "Masukkan nilai"}">`;
}

function promoEligibilityValueInputMarkup(prefix, condition, field) {
    if (field.type === "customer") {
        const count = promoEligibilityRulesCustomerIds({ version: 1, operator: "all", conditions: [condition] }).length;
        return `<div class="promo-eligibility-customer-value"><strong>${count.toLocaleString("id-ID")} akun dipilih</strong><small>Gunakan selector pelanggan aman di bawah daftar kondisi.</small></div>`;
    }
    if (["is_true", "is_false"].includes(condition.operator)) {
        return `<div class="promo-eligibility-unary-value">${condition.operator === "is_true" ? "Ya" : "Tidak"}</div>`;
    }
    if (condition.operator === "between") {
        return `<div class="promo-eligibility-between"><div><span>Minimum</span>${promoEligibilityScalarInputMarkup(prefix, condition, field, "min")}</div><div><span>Maksimum</span>${promoEligibilityScalarInputMarkup(prefix, condition, field, "max")}</div></div>`;
    }
    return promoEligibilityScalarInputMarkup(prefix, condition, field);
}

function promoEligibilityNeedsCustomerSelector(prefix) {
    const state = promoEligibilityStates.get(prefix);
    return !!state?.rules?.conditions?.some((condition) => condition.field === "customer_id");
}

function updatePromoEligibilitySummary(prefix) {
    const state = promoEligibilityStates.get(prefix);
    if (!state) return;
    const output = byId(`${prefix}_eligibility_summary`);
    if (output) output.textContent = state.blockedReason && state.serverSummary
        ? state.serverSummary
        : promoEligibilitySummary(state.rules, state.preset, promoEligibilityMetadata() || promoEligibilityFallbackMetadata());
    renderPromoReview(prefix);
}

function renderPromoEligibilityBuilder(prefix) {
    const metadata = promoEligibilityMetadata();
    const modal = byId(prefix === "promo" ? "addPromoModal" : "editPromoModal");
    const builder = modal?.querySelector("[data-promo-eligibility-builder]");
    const legacyBlock = modal?.querySelector("[data-promo-legacy-segment]");
    if (!metadata) {
        if (builder) builder.hidden = true;
        if (legacyBlock) legacyBlock.hidden = false;
        return;
    }
    if (builder) builder.hidden = false;
    if (legacyBlock) legacyBlock.hidden = true;
    const state = promoEligibilityState(prefix);
    state.blockedReason = promoEligibilityUnsupportedReason(state.rules, metadata, state.blockedReason);
    const preset = byId(`${prefix}_eligibility_preset`);
    if (preset) {
        preset.innerHTML = promoEligibilityPresetOptionMarkup(state.preset);
        preset.value = promoEligibilityPresetRows(metadata).some((item) => item.value === state.preset) ? state.preset : "custom";
        preset.disabled = !!state.blockedReason;
    }
    const presetHelp = byId(`${prefix}_eligibility_preset_help`);
    if (presetHelp) {
        const definition = promoEligibilityPresetRows(metadata).find((item) => item.value === state.preset);
        presetHelp.textContent = definition?.description || "Preset mengisi daftar syarat dan tetap dapat diperiksa atau disesuaikan.";
    }
    const groupOperator = byId(`${prefix}_eligibility_operator`);
    if (groupOperator) {
        groupOperator.innerHTML = metadata.group_operators.map((item) => `<option value="${escapeHtml(item.value)}"${item.value === state.rules.operator ? " selected" : ""}>${escapeHtml(item.label)}</option>`).join("");
        groupOperator.value = state.rules.operator;
        groupOperator.disabled = !!state.blockedReason;
    }
    const legacyNotice = byId(`${prefix}_eligibility_legacy_notice`);
    if (legacyNotice) legacyNotice.hidden = state.source !== "legacy_adapter";
    const blockedNotice = byId(`${prefix}_eligibility_blocked_notice`);
    if (blockedNotice) {
        blockedNotice.hidden = !state.blockedReason;
        blockedNotice.textContent = state.blockedReason ? `${state.blockedReason} Penyimpanan diblokir agar aturan lama tidak hilang.` : "";
    }
    const container = byId(`${prefix}_eligibility_conditions`);
    if (container) {
        if (state.blockedReason) {
            container.innerHTML = `<div class="promo-eligibility-empty">Aturan tersimpan hanya dapat dilihat. Gunakan dashboard yang mendukung versi aturan ini sebelum mengubah promo.</div>`;
        } else if (!state.rules.conditions.length) {
            container.innerHTML = `<div class="promo-eligibility-empty">Tidak ada syarat khusus. Promo berlaku untuk semua pelanggan.</div>`;
        } else {
            container.innerHTML = state.rules.conditions.map((condition, index) => {
                const field = promoEligibilityFieldMeta(condition.field, metadata);
                return `<div class="promo-eligibility-condition" data-promo-eligibility-condition="${escapeHtml(condition._key)}">
                    <div class="promo-eligibility-condition-number">${index + 1}</div>
                    <div><label class="form-label" for="${prefix}_eligibility_${condition._key}_field">Field</label><select id="${prefix}_eligibility_${condition._key}_field" class="form-select" data-promo-eligibility-field>${promoEligibilityFieldOptionMarkup(condition.field, metadata)}</select></div>
                    <div><label class="form-label" for="${prefix}_eligibility_${condition._key}_operator">Operator</label><select id="${prefix}_eligibility_${condition._key}_operator" class="form-select" data-promo-eligibility-condition-operator>${promoEligibilityOperatorOptionMarkup(field, condition.operator)}</select></div>
                    <div class="promo-eligibility-condition-value"><label class="form-label">Nilai</label>${promoEligibilityValueInputMarkup(prefix, condition, field)}</div>
                    <button type="button" class="btn btn-sm btn-outline-danger promo-eligibility-remove" data-promo-eligibility-remove aria-label="Hapus syarat ${index + 1}">Hapus</button>
                </div>`;
            }).join("");
        }
    }
    const add = byId(`${prefix}_eligibility_add`);
    if (add) add.disabled = !!state.blockedReason || state.rules.conditions.length >= metadata.max_conditions || !metadata.fields.length;
    updatePromoEligibilitySummary(prefix);
}

function markPromoEligibilityCustom(prefix) {
    const state = promoEligibilityState(prefix);
    state.preset = "custom";
    state.source = "explicit";
    state.dirty = true;
    state.serverSummary = "";
    const segment = byId(`${prefix}_customer_segment`);
    if (segment) segment.value = "all";
}

function applyPromoEligibilityPreset(prefix, presetValue) {
    const metadata = promoEligibilityMetadata();
    if (!metadata) return;
    const preset = canonicalPromoEligibilityPreset(presetValue);
    const state = promoEligibilityState(prefix);
    if (preset === "custom") {
        markPromoEligibilityCustom(prefix);
        renderPromoEligibilityBuilder(prefix);
        updatePromoConditionalFields(prefix);
        return;
    }
    const definition = promoEligibilityPresetRows(metadata).find((item) => item.value === preset);
    if (!definition?.rules) return;
    const rules = clonePromoEligibilityValue(definition.rules);
    if (preset === "specific") {
        rules.conditions = [{ field: "customer_id", operator: "in", value: promoCustomerIds(prefix) }];
    }
    state.rules = withPromoEligibilityKeys(rules);
    state.preset = preset;
    state.source = "explicit";
    state.dirty = true;
    state.blockedReason = "";
    state.serverSummary = "";
    const segment = byId(`${prefix}_customer_segment`);
    if (segment) segment.value = legacyCustomerSegmentFromPreset(preset);
    if (!state.rules.conditions.some((condition) => condition.field === "customer_id")) clearPromoCustomerSelections(prefix);
    renderPromoEligibilityBuilder(prefix);
    updatePromoConditionalFields(prefix);
}

function promoEligibilityConditionByKey(prefix, key) {
    return promoEligibilityState(prefix).rules.conditions.find((condition) => condition._key === key) || null;
}

function updatePromoEligibilityConditionValue(prefix, condition, field, target) {
    if (target.matches("[data-promo-eligibility-value-min], [data-promo-eligibility-value-max]")) {
        const values = listValue(condition.value);
        while (values.length < 2) values.push("");
        values[target.matches("[data-promo-eligibility-value-min]") ? 0 : 1] = target.value;
        condition.value = values;
    } else if (target.matches("[data-promo-eligibility-value]")) {
        if (target.multiple) condition.value = Array.from(target.selectedOptions || []).map((option) => option.value);
        else if (field.type === "boolean") condition.value = target.value === "true";
        else condition.value = target.value;
    }
    markPromoEligibilityCustom(prefix);
    updatePromoEligibilitySummary(prefix);
}

function bindPromoEligibilityBuilder(prefix) {
    const preset = byId(`${prefix}_eligibility_preset`);
    const operator = byId(`${prefix}_eligibility_operator`);
    const container = byId(`${prefix}_eligibility_conditions`);
    const add = byId(`${prefix}_eligibility_add`);
    if (!preset || preset.dataset.promoEligibilityBound === "1") return;
    preset.dataset.promoEligibilityBound = "1";
    preset.addEventListener("change", () => applyPromoEligibilityPreset(prefix, preset.value));
    operator?.addEventListener("change", () => {
        const state = promoEligibilityState(prefix);
        state.rules.operator = operator.value === "any" ? "any" : "all";
        markPromoEligibilityCustom(prefix);
        renderPromoEligibilityBuilder(prefix);
    });
    add?.addEventListener("click", () => {
        const metadata = promoEligibilityMetadata();
        const state = promoEligibilityState(prefix);
        if (!metadata || state.blockedReason || state.rules.conditions.length >= metadata.max_conditions) return;
        const condition = createPromoEligibilityCondition(metadata.fields[0]);
        if (!condition) return;
        state.rules.conditions.push(condition);
        markPromoEligibilityCustom(prefix);
        renderPromoEligibilityBuilder(prefix);
        updatePromoConditionalFields(prefix);
    });
    container?.addEventListener("click", (event) => {
        const remove = event.target.closest("[data-promo-eligibility-remove]");
        const row = event.target.closest("[data-promo-eligibility-condition]");
        if (!remove || !row) return;
        const state = promoEligibilityState(prefix);
        const removed = promoEligibilityConditionByKey(prefix, row.dataset.promoEligibilityCondition);
        state.rules.conditions = state.rules.conditions.filter((condition) => condition._key !== row.dataset.promoEligibilityCondition);
        markPromoEligibilityCustom(prefix);
        if (removed?.field === "customer_id" && !state.rules.conditions.some((condition) => condition.field === "customer_id")) clearPromoCustomerSelections(prefix);
        renderPromoEligibilityBuilder(prefix);
        updatePromoConditionalFields(prefix);
    });
    const updateCondition = (event) => {
        const target = event.target;
        const row = target.closest("[data-promo-eligibility-condition]");
        if (!row) return;
        const condition = promoEligibilityConditionByKey(prefix, row.dataset.promoEligibilityCondition);
        const metadata = promoEligibilityMetadata();
        if (!condition || !metadata) return;
        if (target.matches("[data-promo-eligibility-field]")) {
            const previousField = condition.field;
            const field = promoEligibilityFieldMeta(target.value, metadata);
            if (!field) return;
            condition.field = field.value;
            condition.operator = field.operators[0] || "equals";
            condition.value = defaultPromoEligibilityValue(field, condition.operator);
            markPromoEligibilityCustom(prefix);
            if (previousField === "customer_id" && !promoEligibilityNeedsCustomerSelector(prefix)) clearPromoCustomerSelections(prefix);
            renderPromoEligibilityBuilder(prefix);
            updatePromoConditionalFields(prefix);
            return;
        }
        const field = promoEligibilityFieldMeta(condition.field, metadata);
        if (!field) return;
        if (target.matches("[data-promo-eligibility-condition-operator]")) {
            condition.operator = target.value;
            condition.value = defaultPromoEligibilityValue(field, condition.operator);
            markPromoEligibilityCustom(prefix);
            renderPromoEligibilityBuilder(prefix);
            return;
        }
        updatePromoEligibilityConditionValue(prefix, condition, field, target);
    };
    container?.addEventListener("input", updateCondition);
    container?.addEventListener("change", updateCondition);
}

function coercePromoEligibilityScalar(value, field) {
    if (value && typeof value === "object") throw new Error(`${field.label} harus berupa nilai sederhana.`);
    if (["integer", "money"].includes(field.type)) {
        const text = String(value ?? "").trim();
        if (!/^-?\d+$/.test(text)) throw new Error(`${field.label} harus berupa angka bulat.`);
        const number = Number(text);
        if (!Number.isSafeInteger(number)) throw new Error(`${field.label} memiliki angka tidak valid.`);
        if (field.min !== null && field.min !== undefined && number < Number(field.min)) throw new Error(`${field.label} minimal ${field.min}.`);
        if (field.max !== null && field.max !== undefined && number > Number(field.max)) throw new Error(`${field.label} maksimal ${field.max}.`);
        return number;
    }
    if (field.type === "boolean") return value === true || value === 1 || value === "1" || String(value).toLowerCase() === "true";
    if (field.type === "datetime") {
        const parsed = new Date(String(value || ""));
        if (Number.isNaN(parsed.getTime())) throw new Error(`${field.label} wajib berisi tanggal dan waktu yang valid.`);
        return parsed.toISOString();
    }
    let text = String(value ?? "").trim();
    if (!text) throw new Error(`${field.label} wajib diisi.`);
    if (field.type === "enum" && field.options.length && !field.options.some((option) => String(option.value) === text)) throw new Error(`${field.label} memiliki pilihan tidak dikenal.`);
    if (field.value === "normalized_phone") {
        const digits = text.replace(/\D/g, "");
        text = digits.startsWith("0") ? `62${digits.slice(1)}` : digits;
        if (text.length < 6 || text.length > 20) throw new Error(`${field.label} harus berupa nomor WhatsApp yang valid.`);
    }
    if (field.value === "target_id") {
        if (text.length > 255) throw new Error(`${field.label} maksimal 255 karakter.`);
        text = text.toLowerCase();
    }
    return text;
}

function coercePromoEligibilityValue(condition, field, prefix) {
    if (field.type === "customer") {
        const ids = promoCustomerIds(prefix);
        if (!ids.length) throw new Error("Pilih minimal satu akun untuk syarat pelanggan tertentu.");
        if (!["in", "not_in"].includes(condition.operator) && ids.length !== 1) {
            throw new Error("Operator akun customer ini hanya dapat memakai tepat satu akun.");
        }
        return ["in", "not_in"].includes(condition.operator) ? ids : ids[0];
    }
    if (["is_true", "is_false"].includes(condition.operator)) return null;
    if (condition.operator === "between") {
        const values = listValue(condition.value);
        if (values.length !== 2) throw new Error(`${field.label} membutuhkan nilai minimum dan maksimum.`);
        const coerced = values.map((value) => coercePromoEligibilityScalar(value, field));
        const comparable = field.type === "datetime" ? coerced.map((value) => new Date(value).getTime()) : coerced.map(Number);
        if (comparable[0] > comparable[1]) throw new Error(`Nilai minimum ${field.label} tidak boleh melebihi maksimum.`);
        return coerced;
    }
    if (["in", "not_in"].includes(condition.operator)) {
        const rawValues = Array.isArray(condition.value)
            ? condition.value
            : String(condition.value ?? "").split(",").map((value) => value.trim()).filter(Boolean);
        if (!rawValues.length || rawValues.length > 100) throw new Error(`${field.label} membutuhkan 1 sampai 100 nilai.`);
        return [...new Set(rawValues.map((value) => coercePromoEligibilityScalar(value, field)))];
    }
    return coercePromoEligibilityScalar(condition.value, field);
}

function serializePromoEligibilityRules(prefix) {
    const metadata = promoEligibilityMetadata();
    if (!metadata) return null;
    const state = promoEligibilityState(prefix);
    const blocked = promoEligibilityUnsupportedReason(state.rules, metadata, state.blockedReason);
    if (blocked) throw new Error(blocked);
    if (state.rules.conditions.length > metadata.max_conditions) throw new Error(`Maksimal ${metadata.max_conditions} syarat pelanggan.`);
    if (state.rules.operator === "any" && !state.rules.conditions.length) throw new Error("Operator SALAH SATU membutuhkan minimal satu syarat.");
    const customerConditions = state.rules.conditions.filter((condition) => condition.field === "customer_id");
    if (customerConditions.length > 1) throw new Error("Gunakan maksimal satu syarat akun customer tertentu.");
    const conditions = state.rules.conditions.map((condition) => {
        const field = promoEligibilityFieldMeta(condition.field, metadata);
        if (!field) throw new Error("Terdapat field syarat pelanggan yang tidak dikenal.");
        if (!field.operators.includes(condition.operator)) throw new Error(`Operator ${field.label} tidak diizinkan.`);
        return { field: field.value, operator: condition.operator, value: coercePromoEligibilityValue(condition, field, prefix) };
    });
    const canonical = { version: 1, operator: state.rules.operator === "any" ? "any" : "all", conditions };
    const encoded = JSON.stringify(canonical);
    const byteLength = typeof TextEncoder !== "undefined"
        ? new TextEncoder().encode(encoded).length
        : unescape(encodeURIComponent(encoded)).length;
    if (byteLength > metadata.max_bytes) throw new Error(`Aturan eligibility melebihi batas ${metadata.max_bytes.toLocaleString("id-ID")} byte.`);
    return canonical;
}

function syncPromoEligibilityCustomerCondition(prefix, ids) {
    const state = promoEligibilityStates.get(prefix);
    if (!state || !promoEligibilityMetadata()) return;
    const canonicalIds = [...new Set(listValue(ids).map(Number).filter((value) => Number.isInteger(value) && value > 0))];
    let changed = false;
    state.rules.conditions.forEach((condition) => {
        if (condition.field !== "customer_id") return;
        if (JSON.stringify(listValue(condition.value).map(Number)) !== JSON.stringify(canonicalIds)) changed = true;
        condition.value = canonicalIds;
    });
    if (changed) {
        state.source = "explicit";
        state.dirty = true;
        state.serverSummary = "";
        updatePromoEligibilitySummary(prefix);
        const countNodes = byId(`${prefix}_eligibility_conditions`)?.querySelectorAll(".promo-eligibility-customer-value strong") || [];
        countNodes.forEach((node) => { node.textContent = `${canonicalIds.length.toLocaleString("id-ID")} akun dipilih`; });
    }
}

function populatePromoEligibility(prefix, promo = {}) {
    const metadata = promoEligibilityMetadata();
    renderPromoEligibilityBuilder(prefix);
    if (!metadata) return;
    const relationValues = listValue(promo.customer_ids).length ? promo.customer_ids : promo.customer_targets;
    const relationIds = [...new Set(listValue(relationValues).map((value) => Number(value?.id ?? value)).filter((value) => Number.isInteger(value) && value > 0))];
    const parsed = parsePromoEligibilityRules(promo.eligibility_rules);
    const source = String(promo.eligibility_source || (parsed || !promo.id ? "explicit" : "legacy_adapter")).toLowerCase();
    const legacySegment = canonicalPromoCustomerSegment(promo.eligibility_legacy_segment || promo.customer_segment || "all");
    const effective = parsed || parsePromoEligibilityRules(legacyPromoEligibilityRules(legacySegment, relationIds));
    const ruleCustomerIds = promoEligibilityRulesCustomerIds(effective);
    let extraReason = ["explicit", "legacy_adapter"].includes(source) ? "" : "Sumber aturan eligibility tidak dikenali.";
    if (source === "explicit" && promoEligibilityHasCustomerCondition(effective)) {
        const relationSet = [...relationIds].sort((a, b) => a - b);
        const ruleSet = [...ruleCustomerIds].sort((a, b) => a - b);
        if (JSON.stringify(relationSet) !== JSON.stringify(ruleSet)) extraReason = "Target customer pada rule dan relasi promo tidak konsisten.";
    }
    const preset = inferPromoEligibilityPreset(effective, source, legacySegment);
    const keyed = withPromoEligibilityKeys(effective);
    promoEligibilityStates.set(prefix, {
        rules: keyed,
        preset,
        source: source === "legacy_adapter" ? "legacy_adapter" : "explicit",
        legacySegment,
        dirty: false,
        blockedReason: promoEligibilityUnsupportedReason({ ...effective, conditions: keyed.conditions }, metadata, extraReason),
        serverSummary: String(promo.eligibility_summary || "")
    });
    const segment = byId(`${prefix}_customer_segment`);
    if (segment) segment.value = source === "legacy_adapter" ? legacySegment : legacyCustomerSegmentFromPreset(preset);
    renderPromoEligibilityBuilder(prefix);
}

function normalizePromoCustomerOption(item) {
    const source = item && typeof item === "object" ? item : { id: item };
    const id = Number(source.customer_id ?? source.id ?? source.value);
    if (!Number.isInteger(id) || id <= 0) return null;
    const activeValue = source.account_active ?? source.active ?? source.is_active;
    const inactiveValues = new Set([false, 0, "0", "false", "inactive", "disabled"]);
    const active = activeValue === undefined || activeValue === null
        ? true
        : !inactiveValues.has(typeof activeValue === "string" ? activeValue.trim().toLowerCase() : activeValue);
    const successCount = Number(source.successCount ?? source.success_count ?? source.successful_order_count ?? source.success_orders ?? 0);
    const phone = String(source.phone ?? source.customer_phone ?? "").trim();
    return {
        id,
        name: String(source.customer_name ?? source.name ?? source.label ?? "").trim(),
        phone,
        phoneMasked: String(source.phoneMasked ?? source.phone_masked ?? source.masked_phone ?? phone).trim(),
        email: String(source.customer_email ?? source.email ?? "").trim(),
        active,
        successCount: Number.isFinite(successCount) ? Math.max(0, successCount) : 0,
    };
}

function filterPromoCustomerOptions(rows, query = "") {
    const normalizedQuery = String(query || "").trim().toLocaleLowerCase("id-ID");
    return listValue(rows)
        .map(normalizePromoCustomerOption)
        .filter(Boolean)
        .filter((item) => {
            if (!normalizedQuery) return true;
            return [item.id, item.name, item.phone, item.phoneMasked, item.email]
                .some((value) => String(value || "").toLocaleLowerCase("id-ID").includes(normalizedQuery));
        });
}

function promoCustomerOptionRows({ simulation = false } = {}) {
    const configured = simulation
        ? (promoOptions?.simulation_customer_options || promoOptions?.customer_options || [])
        : (promoOptions?.customer_options || []);
    const unique = new Map();
    filterPromoCustomerOptions(configured).forEach((item) => {
        if (simulation || item.active) unique.set(item.id, item);
    });
    return Array.from(unique.values());
}

function mergePromoCustomerOptions(rows, { simulation = false, includeInactive = false } = {}) {
    if (!promoOptions) return [];
    const key = simulation ? "simulation_customer_options" : "customer_options";
    const merged = new Map(
        filterPromoCustomerOptions(promoOptions[key] || []).map((item) => [item.id, item])
    );
    const normalizedRows = filterPromoCustomerOptions(rows);
    normalizedRows.forEach((item) => {
        if (simulation || includeInactive || item.active) merged.set(item.id, item);
    });
    promoOptions[key] = Array.from(merged.values());
    return normalizedRows;
}

async function fetchPromoCustomerOptions({ query = "", ids = [], includeInactive = false } = {}) {
    const params = new URLSearchParams();
    const cleanedQuery = String(query || "").trim();
    const canonicalIds = [...new Set(listValue(ids).map(Number).filter((id) => Number.isInteger(id) && id > 0))];
    if (cleanedQuery) params.set("q", cleanedQuery);
    if (canonicalIds.length) params.set("ids", canonicalIds.join(","));
    if (includeInactive) params.set("include_inactive", "true");
    params.set("limit", "50");
    const response = await api(`/admin/api/promos/customer-options?${params.toString()}`);
    const rows = Array.isArray(response) ? response : (response?.items || []);
    return {
        items: filterPromoCustomerOptions(rows),
        hasMore: !!response?.has_more,
    };
}

async function hydratePromoCustomerSelections(prefix, values = []) {
    const ids = [...new Set(listValue(values).map((value) => Number(value?.id ?? value)).filter((id) => Number.isInteger(id) && id > 0))];
    if (!ids.length) return;
    try {
        const response = await fetchPromoCustomerOptions({ ids, includeInactive: true });
        const items = mergePromoCustomerOptions(response.items, { includeInactive: true });
        const selected = promoCustomerSelection(prefix);
        items.forEach((item) => {
            if (selected.has(item.id)) selected.set(item.id, item);
        });
        syncPromoCustomerSelection(prefix);
    } catch (error) {
        console.warn("Detail target pelanggan promo belum dapat dimuat.", error);
    }
}

async function searchPromoCustomers(prefix, query) {
    const cleanedQuery = String(query || "").trim();
    const sequence = (promoCustomerSearchSequences.get(prefix) || 0) + 1;
    promoCustomerSearchSequences.set(prefix, sequence);
    const output = byId(`${prefix}_customer_search_results`);
    if (!cleanedQuery) {
        promoCustomerRemoteResults.delete(prefix);
        renderPromoCustomerSearchResults(prefix);
        return;
    }
    if (output) output.innerHTML = `<div class="list-group-item small text-muted">Mencari pelanggan...</div>`;
    try {
        const response = await fetchPromoCustomerOptions({ query: cleanedQuery });
        if (promoCustomerSearchSequences.get(prefix) !== sequence) return;
        const items = mergePromoCustomerOptions(response.items);
        promoCustomerRemoteResults.set(prefix, { query: cleanedQuery, items, hasMore: response.hasMore });
        renderPromoCustomerSearchResults(prefix);
    } catch (error) {
        if (promoCustomerSearchSequences.get(prefix) !== sequence) return;
        promoCustomerRemoteResults.delete(prefix);
        if (output) output.innerHTML = `<div class="list-group-item small text-danger">Pencarian pelanggan gagal. Coba lagi.</div>`;
        console.warn("Pencarian pelanggan promo gagal.", error);
    }
}

function promoCustomerOptionLabel(customer, { includeStatus = true } = {}) {
    const normalized = normalizePromoCustomerOption(customer);
    if (!normalized) return "Pelanggan tidak dikenal";
    const identity = normalized.name || normalized.phoneMasked || `Akun #${normalized.id}`;
    const supporting = [normalized.phoneMasked]
        .filter((value) => value && value !== identity)
        .join(" • ");
    return `${identity}${supporting ? ` — ${supporting}` : ""}${includeStatus && !normalized.active ? " (akun nonaktif)" : ""}`;
}

function promoCustomerSelection(prefix) {
    if (!promoCustomerSelections.has(prefix)) promoCustomerSelections.set(prefix, new Map());
    return promoCustomerSelections.get(prefix);
}

function promoCustomerIds(prefix) {
    const stateIds = Array.from(promoCustomerSelection(prefix).keys());
    if (stateIds.length) return stateIds;
    const raw = String(byId(`${prefix}_customer_ids`)?.value || "").trim();
    if (!raw) return [];
    return uniqueIntegerArray(raw.split(/[,;\s]+/), "Target Pelanggan");
}

function renderPromoCustomerSearchResults(prefix) {
    const output = byId(`${prefix}_customer_search_results`);
    if (!output) return;
    const query = byId(`${prefix}_customer_search`)?.value || "";
    const selected = promoCustomerSelection(prefix);
    const eligibilityCustomerLimitReached = !!promoEligibilityMetadata()
        && promoEligibilityNeedsCustomerSelector(prefix)
        && selected.size >= 100;
    const remote = promoCustomerRemoteResults.get(prefix);
    const rows = (
        remote && remote.query === String(query || "").trim()
            ? remote.items
            : filterPromoCustomerOptions(promoCustomerOptionRows(), query)
    ).slice(0, 30);
    if (!rows.length) {
        output.innerHTML = `<div class="list-group-item small text-muted">Tidak ada akun aktif yang cocok.</div>`;
        return;
    }
    output.innerHTML = rows.map((customer) => {
        const isSelected = selected.has(customer.id);
        const disabled = isSelected || eligibilityCustomerLimitReached;
        return `<button type="button" class="list-group-item list-group-item-action d-flex justify-content-between align-items-center gap-3" data-promo-customer-add="${customer.id}"${disabled ? " disabled" : ""}><span class="text-start"><strong>${escapeHtml(customer.name || `Akun #${customer.id}`)}</strong><small class="d-block text-muted">${escapeHtml([customer.phoneMasked, `ID ${customer.id}`].filter(Boolean).join(" • "))}</small></span><span class="badge ${isSelected ? "bg-success" : (eligibilityCustomerLimitReached ? "bg-secondary" : "bg-primary")}">${isSelected ? "Dipilih" : (eligibilityCustomerLimitReached ? "Batas 100 akun" : "Pilih")}</span></button>`;
    }).join("") + (remote?.hasMore ? `<div class="list-group-item small text-muted">Persempit kata pencarian untuk hasil lainnya.</div>` : "");
}

function syncPromoCustomerSelection(prefix) {
    const selected = promoCustomerSelection(prefix);
    const ids = Array.from(selected.keys());
    const hidden = byId(`${prefix}_customer_ids`);
    if (hidden) hidden.value = ids.join(",");
    const count = byId(`${prefix}_customer_selected_count`);
    if (count) count.textContent = `${ids.length.toLocaleString("id-ID")} pelanggan dipilih`;
    const output = byId(`${prefix}_customer_selected`);
    if (output) {
        output.innerHTML = ids.length
            ? Array.from(selected.values()).map((customer) => `<span class="badge rounded-pill bg-light text-dark border d-inline-flex align-items-center gap-2"><span>${escapeHtml(promoCustomerOptionLabel(customer, { includeStatus: false }))}</span><button type="button" class="btn btn-sm p-0 border-0 text-danger" data-promo-customer-remove="${customer.id}" aria-label="Hapus ${escapeHtml(customer.name || `akun ${customer.id}`)}">×</button></span>`).join("")
            : `<span class="small text-muted">Belum ada pelanggan dipilih.</span>`;
    }
    syncPromoEligibilityCustomerCondition(prefix, ids);
    renderPromoCustomerSearchResults(prefix);
    renderPromoReview(prefix);
}

function setPromoCustomerSelections(prefix, values = []) {
    const available = new Map(promoCustomerOptionRows().map((item) => [item.id, item]));
    const selected = promoCustomerSelection(prefix);
    selected.clear();
    listValue(values).forEach((value) => {
        const normalized = normalizePromoCustomerOption(value);
        const id = normalized?.id ?? Number(value);
        if (!Number.isInteger(id) || id <= 0 || selected.has(id)) return;
        selected.set(id, available.get(id) || normalized || { id, name: `Akun #${id}`, phone: "", phoneMasked: "", email: "", active: true, successCount: 0 });
    });
    syncPromoCustomerSelection(prefix);
}

function clearPromoCustomerSelections(prefix) {
    promoCustomerSelection(prefix).clear();
    promoCustomerRemoteResults.delete(prefix);
    promoCustomerSearchSequences.set(prefix, (promoCustomerSearchSequences.get(prefix) || 0) + 1);
    const timer = promoCustomerSearchTimers.get(prefix);
    if (timer) clearTimeout(timer);
    promoCustomerSearchTimers.delete(prefix);
    const search = byId(`${prefix}_customer_search`);
    if (search) search.value = "";
    syncPromoCustomerSelection(prefix);
}

function bindPromoCustomerSelector(prefix) {
    const search = byId(`${prefix}_customer_search`);
    const results = byId(`${prefix}_customer_search_results`);
    const selectedOutput = byId(`${prefix}_customer_selected`);
    if (!search || search.dataset.promoBound === "1") return;
    search.dataset.promoBound = "1";
    search.addEventListener("input", () => {
        const timer = promoCustomerSearchTimers.get(prefix);
        if (timer) clearTimeout(timer);
        const query = search.value;
        if (!String(query || "").trim()) {
            void searchPromoCustomers(prefix, "");
            return;
        }
        const nextTimer = setTimeout(() => {
            promoCustomerSearchTimers.delete(prefix);
            void searchPromoCustomers(prefix, query);
        }, 250);
        promoCustomerSearchTimers.set(prefix, nextTimer);
    });
    results?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-promo-customer-add]");
        if (!button) return;
        const id = Number(button.dataset.promoCustomerAdd || 0);
        const customer = promoCustomerOptionRows().find((item) => item.id === id);
        if (!customer) return;
        if (promoEligibilityMetadata() && promoEligibilityNeedsCustomerSelector(prefix) && promoCustomerSelection(prefix).size >= 100) return;
        promoCustomerSelection(prefix).set(id, customer);
        syncPromoCustomerSelection(prefix);
    });
    selectedOutput?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-promo-customer-remove]");
        if (!button) return;
        promoCustomerSelection(prefix).delete(Number(button.dataset.promoCustomerRemove || 0));
        syncPromoCustomerSelection(prefix);
    });
    renderPromoCustomerSearchResults(prefix);
}

async function ensurePromoOptions() {
    if (promoOptions) return promoOptions;
    try {
        promoOptions = await api("/admin/api/promos/options");
        if (!Array.isArray(promoOptions?.customer_options) || !Array.isArray(promoOptions?.simulation_customer_options)) {
            const configuredCustomerOptions = Array.isArray(promoOptions?.customer_options) ? promoOptions.customer_options : null;
            const configuredSimulationOptions = Array.isArray(promoOptions?.simulation_customer_options) ? promoOptions.simulation_customer_options : null;
            promoOptions.customer_options = configuredCustomerOptions || [];
            promoOptions.simulation_customer_options = configuredSimulationOptions || [];
        }
    } catch (error) {
        promoOptions = {
            categories: [],
            providers: [],
            skus: [],
            payment_methods: [
                { value: "QRIS", label: "QRIS" },
                { value: "DANA", label: "DANA" },
                { value: "OVO", label: "OVO" },
                { value: "WALLET", label: "Wallet LIXAFA" }
            ],
            customer_segments: PROMO_CUSTOMER_SEGMENT_DEFAULTS,
            customer_options: [],
            simulation_customer_options: []
        };
    }
    return promoOptions;
}

function promoTargetOptions(scope) {
    const options = promoOptions || {};
    const catalog = Array.isArray(options.target_options) ? options.target_options : [];
    if (catalog.length && ["category", "provider", "sku"].includes(scope)) {
        return catalog
            .filter((item) => String(item.target_type || "").toLowerCase() === scope)
            .map((item) => ({
                ...item,
                value: item.target_key ?? item.value ?? "",
                label: item.label || item.target_key || item.value || ""
            }));
    }
    if (scope === "category") return options.categories || [];
    if (scope === "provider") return options.providers || [];
    if (scope === "sku") return options.skus || [];
    return [];
}

function populatePromoTarget(prefix, selectedValue = "") {
    const scope = byId(`${prefix}_target_scope`)?.value || "all";
    const select = byId(`${prefix}_target_value`);
    if (!select) return;
    const rows = promoTargetOptions(scope);
    if (scope === "all") {
        select.innerHTML = `<option value="">Semua produk</option>`;
        select.disabled = true;
        return;
    }
    select.disabled = false;
    select.innerHTML = `<option value="">Pilih target...</option>${rows.map((item) => {
        const value = item.value || item.sku || "";
        const label = item.label || value;
        const meta = item.provider && scope === "sku" ? ` - ${item.provider}` : "";
        const optionId = Number(item.id);
        const idAttribute = Number.isInteger(optionId) && optionId > 0 ? ` data-option-id="${optionId}"` : "";
        return `<option value="${escapeHtml(value)}"${idAttribute} ${String(value) === String(selectedValue || "") ? "selected" : ""}>${escapeHtml(label + meta)}</option>`;
    }).join("")}`;
    if (selectedValue && !rows.some((item) => String(item.value || item.sku || "") === String(selectedValue))) {
        select.insertAdjacentHTML("beforeend", `<option value="${escapeHtml(selectedValue)}" selected>${escapeHtml(selectedValue)} (manual lama)</option>`);
    }
}

function selectedMultiValues(id) {
    const element = byId(id);
    if (!element) return [];
    if (element.selectedOptions) {
        return Array.from(element.selectedOptions || []).map((option) => option.value).filter(Boolean);
    }
    return Array.from(element.querySelectorAll("input[type='checkbox']:checked"))
        .map((input) => input.value)
        .filter(Boolean);
}

function selectedMultiLabels(id) {
    const element = byId(id);
    if (!element?.selectedOptions) return [];
    return Array.from(element.selectedOptions).map((option) => option.textContent.trim()).filter(Boolean);
}

function selectedPrimaryTargetOptionId(prefix) {
    const select = byId(`${prefix}_target_value`);
    const raw = select?.selectedOptions?.[0]?.dataset?.optionId;
    if (raw === "" || raw === null || raw === undefined) return null;
    const parsed = Number(raw);
    if (!Number.isInteger(parsed) || parsed <= 0) {
        throw new Error("Target Promo utama memiliki ID tidak valid.");
    }
    return parsed;
}

function populatePromoPaymentMethods(prefix, selectedValues = []) {
    const container = byId(`${prefix}_payment_methods`);
    if (!container) return;
    const selected = new Set((selectedValues || []).map((value) => String(value).toUpperCase()));
    const methods = ((promoOptions?.payment_methods || []).length ? promoOptions.payment_methods : [
        { value: "QRIS", label: "QRIS" },
        { value: "DANA", label: "DANA" },
        { value: "OVO", label: "OVO" }
    ]).map((item) => ({ ...item }));
    const availableValues = new Set(methods.map((item) => String(item.value || "").toUpperCase()));
    selected.forEach((value) => {
        if (!availableValues.has(value)) {
            methods.push({ value, label: `${value} (metode legacy)`, group: "Dipertahankan untuk kompatibilitas", legacy: true });
        }
    });
    container.innerHTML = `
        <div class="row g-2" style="max-height: 190px; overflow-y: auto;">
            ${methods.map((item) => {
        const value = String(item.value || "").toUpperCase();
        const label = item.label || value;
        const group = item.group ? `<small class="d-block text-muted">${escapeHtml(item.group)}</small>` : "";
        const legacyBadge = item.legacy ? `<span class="badge bg-warning text-dark ms-1">Legacy</span>` : "";
        const searchText = `${label} ${value} ${item.group || ""}`.toLowerCase();
        return `
            <div class="col-md-6 col-xl-4" data-method-item data-search="${escapeHtml(searchText)}">
                <label class="form-check border rounded-3 p-2 h-100" style="cursor:pointer;">
                    <input class="form-check-input promo-method-check me-2" type="checkbox" value="${escapeHtml(value)}" ${selected.has(value) ? "checked" : ""}>
                    <span class="form-check-label">
                        <strong>${escapeHtml(label)}</strong>${legacyBadge}
                        <small class="d-block text-muted">${escapeHtml(value)}</small>
                        ${group}
                    </span>
                </label>
            </div>`;
    }).join("")}
        </div>`;
    window.filterPromoPaymentMethods(prefix);
}

function setMultiSelectValues(id, values = []) {
    const select = byId(id);
    if (!select) return;
    const selected = new Set((values || []).map((value) => String(value)));
    Array.from(select.options).forEach((option) => {
        option.selected = selected.has(String(option.value));
    });
}

function populatePromoAdvancedTargets(prefix, selectedValues = [], exclusions = []) {
    const scope = byId(`${prefix}_target_scope`)?.value || "all";
    const targetSelect = byId(`${prefix}_target_values`);
    const exclusionSelect = byId(`${prefix}_target_exclusions`);
    if (targetSelect) {
        const rows = promoTargetOptions(scope);
        const selected = new Set((selectedValues || []).map((value) => String(typeof value === "object" && value !== null ? value.id : value)).filter(Boolean));
        const usableRows = rows.filter((item) => Number.isInteger(Number(item.id)) && Number(item.id) > 0);
        const known = new Set(usableRows.map((item) => String(item.id)));
        const legacyRows = Array.from(selected)
            .filter((value) => /^\d+$/.test(value) && !known.has(value))
            .map((value) => ({ id: Number(value), label: `Target #${value} (legacy)` }));
        targetSelect.innerHTML = [...usableRows, ...legacyRows].map((item) => {
            const value = Number(item.id);
            return `<option value="${value}" ${selected.has(String(value)) ? "selected" : ""}>${escapeHtml(item.label || item.target_key || `Target #${value}`)}</option>`;
        }).join("");
        targetSelect.disabled = scope === "all";
    }
    if (exclusionSelect) {
        const rows = promoTargetOptions("sku").filter((item) => Number.isInteger(Number(item.id)) && Number(item.id) > 0);
        const selected = new Set((exclusions || []).map((value) => String(typeof value === "object" && value !== null ? value.id : value)).filter(Boolean));
        const known = new Set(rows.map((item) => String(item.id)));
        const legacyRows = Array.from(selected)
            .filter((value) => /^\d+$/.test(value) && !known.has(value))
            .map((value) => ({ id: Number(value), label: `Pengecualian #${value} (legacy)` }));
        exclusionSelect.innerHTML = [...rows, ...legacyRows].map((item) => {
            const value = Number(item.id);
            return `<option value="${value}" ${selected.has(String(value)) ? "selected" : ""}>${escapeHtml(item.label || item.target_key || `Target #${value}`)}</option>`;
        }).join("");
    }
    renderPromoTargetImpact(prefix);
}

window.clearPromoPaymentMethods = function(prefix = "promo") {
    byId(`${prefix}_payment_methods`)?.querySelectorAll("input[type='checkbox']").forEach((input) => {
        input.checked = false;
    });
};

window.filterPromoPaymentMethods = function(prefix = "promo") {
    const query = (byId(`${prefix}_payment_method_search`)?.value || "").trim().toLowerCase();
    byId(`${prefix}_payment_methods`)?.querySelectorAll("[data-method-item]").forEach((item) => {
        const text = item.getAttribute("data-search") || "";
        item.style.display = !query || text.includes(query) ? "" : "none";
    });
};

function buildPromoSimulationIdentity(mode, customerId, phone, targetId = "") {
    const identityMode = String(mode || "guest").trim().toLowerCase() === "member" ? "member" : "guest";
    const parsedCustomerId = Number(customerId);
    return {
        customer_id: identityMode === "member" && Number.isInteger(parsedCustomerId) && parsedCustomerId > 0
            ? parsedCustomerId
            : null,
        customer_phone: String(phone || "").trim() || null,
        target_id: String(targetId || "").trim() || null,
    };
}

function populatePromoSimulationCustomers(prefix, selectedCustomerId = "") {
    const select = byId(`${prefix}_simulation_customer_id`);
    if (!select) return;
    const selected = Number(selectedCustomerId || 0);
    const rows = promoCustomerOptionRows({ simulation: true });
    const known = new Set(rows.map((item) => item.id));
    const legacy = Number.isInteger(selected) && selected > 0 && !known.has(selected)
        ? [{ id: selected, name: `Akun #${selected}`, phone: "", phoneMasked: "", email: "", active: true, successCount: 0 }]
        : [];
    select.innerHTML = `<option value="">Pilih akun member</option>${[...rows, ...legacy].map((customer) => `<option value="${customer.id}" data-phone="${escapeHtml(customer.phone)}" data-account-active="${customer.active ? "1" : "0"}" data-success-count="${customer.successCount}"${customer.id === selected ? " selected" : ""}>${escapeHtml(promoCustomerOptionLabel(customer))}</option>`).join("")}`;
    if (selected > 0) select.value = String(selected);
}

function updatePromoSimulationIdentityFields(prefix, { accountChanged = false } = {}) {
    const mode = byId(`${prefix}_simulation_identity_mode`)?.value === "member" ? "member" : "guest";
    const customerSelect = byId(`${prefix}_simulation_customer_id`);
    const phone = byId(`${prefix}_simulation_customer_phone`);
    const help = byId(`${prefix}_simulation_identity_help`);
    byId(prefix === "promo" ? "addPromoModal" : "editPromoModal")
        ?.querySelectorAll("[data-promo-simulation-account]")
        .forEach((node) => { node.hidden = mode !== "member"; });
    if (mode === "member") {
        const option = customerSelect?.selectedOptions?.[0];
        const accountPhone = option?.dataset?.phone || "";
        if (phone && (accountChanged || phone.value.trim() !== accountPhone)) phone.value = accountPhone;
        if (phone) phone.readOnly = true;
        if (help) {
            const active = option?.value ? option.dataset.accountActive !== "0" : null;
            help.textContent = option?.value
                ? `${active ? "Akun aktif" : "Akun nonaktif"}; status login dan histori transaksi diverifikasi backend dari akun terpilih.`
                : "Pilih akun member. Status aktif dan histori transaksi diverifikasi backend dan tidak dapat dipilih manual.";
        }
    } else {
        if (phone) phone.readOnly = false;
        if (help) help.textContent = "Guest tidak login. Histori transaksi ditentukan backend dari nomor WhatsApp dan tidak dapat dipilih manual.";
    }
}

function bindPromoSimulationIdentity(prefix) {
    const mode = byId(`${prefix}_simulation_identity_mode`);
    const customer = byId(`${prefix}_simulation_customer_id`);
    if (!mode || mode.dataset.promoBound === "1") return;
    mode.dataset.promoBound = "1";
    mode.addEventListener("change", () => updatePromoSimulationIdentityFields(prefix));
    customer?.addEventListener("change", () => updatePromoSimulationIdentityFields(prefix, { accountChanged: true }));
    updatePromoSimulationIdentityFields(prefix);
}

function diagnosticBooleanLabel(value, trueLabel, falseLabel, missingLabel = "Tidak dilaporkan backend") {
    if (value === true || value === 1 || value === "1" || value === "true") return trueLabel;
    if (value === false || value === 0 || value === "0" || value === "false") return falseLabel;
    return missingLabel;
}

function promoSimulationIdentityStatusLabel(status, isAuthenticated) {
    const normalized = String(status || "").trim().toLowerCase();
    const labels = {
        guest: "Guest tanpa login",
        unauthenticated: "Guest tanpa login",
        member: "Member terautentikasi",
        member_active: "Member aktif terautentikasi",
        member_inactive: "Member terautentikasi, akun nonaktif",
        account_inactive: "Member terautentikasi, akun nonaktif",
        not_found: "Akun member tidak ditemukan",
    };
    return labels[normalized] || diagnosticBooleanLabel(isAuthenticated, "Member terautentikasi", "Guest tanpa login");
}

function promoSimulationHistoryLabel(status, successCount, hasSuccessOrder) {
    const normalized = String(status || "").trim().toLowerCase();
    const statusLabels = {
        new: "Pembeli pertama",
        new_customer: "Pembeli pertama",
        existing: "Pelanggan lama",
        existing_customer: "Pelanggan lama",
        unknown: "Histori belum dapat ditentukan",
        identity_required: "Identitas diperlukan untuk memeriksa histori",
    };
    const count = Number(successCount);
    const label = statusLabels[normalized]
        || diagnosticBooleanLabel(hasSuccessOrder, "Pelanggan lama", "Pembeli pertama", "Histori belum dapat ditentukan");
    return Number.isFinite(count) ? `${label} • ${Math.max(0, count).toLocaleString("id-ID")} transaksi SUCCESS` : label;
}

function promoEligibilitySimulationConditions(data = {}) {
    const eligibilityConditions = data?.diagnostics?.eligibility?.conditions;
    if (Array.isArray(eligibilityConditions)) return eligibilityConditions;
    return listValue(data.conditions || data.checks);
}

function promoEligibilityDiagnosticActual(field, value) {
    if (value === null || value === undefined || value === "") return "Tidak tersedia";
    if (!field) return "Nilai aman tersedia";
    if (["customer_id", "normalized_phone", "target_id"].includes(field.value) && ["present", "missing"].includes(String(value))) {
        return String(value) === "present" ? "Tersedia" : "Tidak tersedia";
    }
    if (value && typeof value === "object" && !Array.isArray(value)) return "Nilai terlindungi tersedia";
    return promoEligibilityValueLabel(field, value);
}

function promoEligibilityDiagnosticCondition(item = {}, index = 0) {
    const metadata = promoEligibilityMetadata() || promoEligibilityFallbackMetadata();
    const field = promoEligibilityFieldMeta(item.field, metadata);
    const operator = String(item.operator || "").trim().toLowerCase();
    const expected = item.expected ?? item.value;
    const matched = item.matched ?? item.passed;
    const result = matched === true ? "Terpenuhi" : (matched === false ? "Tidak terpenuhi" : "Belum dinilai");
    const requirement = field && field.operators.includes(operator)
        ? promoEligibilityConditionSummary({ field: field.value, operator, value: expected }, metadata)
        : `Kondisi ${index + 1} tidak dikenali dashboard`;
    const actualSafe = item.actual_safe ?? item.actual;
    const actual = promoEligibilityDiagnosticActual(field, actualSafe);
    const message = String(item.message || "").trim();
    return {
        label: `Syarat ${index + 1}${field ? ` — ${field.label}` : ""}`,
        value: [result, requirement, actualSafe !== undefined ? `Aktual: ${actual}` : "", message].filter(Boolean).join(" • "),
        matched,
        message: message || requirement
    };
}

function promoEligibilityDiagnosticRows(data = {}) {
    const eligibility = data?.diagnostics?.eligibility;
    if (!eligibility || typeof eligibility !== "object") return [];
    const operator = String(eligibility.operator || "all").toLowerCase();
    const source = String(eligibility.source || data.eligibility_source || "explicit").toLowerCase();
    const eligible = eligibility.eligible ?? data.eligible;
    const rows = [
        { label: "Pola syarat", value: operator === "any" ? "Penuhi SALAH SATU syarat" : (operator === "all" ? "Penuhi SEMUA syarat" : "Pola syarat tidak dikenali") },
        { label: "Sumber syarat", value: source === "legacy_adapter" ? "Adapter promo legacy" : (source === "explicit" ? "Aturan tersimpan" : "Sumber tidak dikenali") },
        { label: "Hasil eligibility", value: diagnosticBooleanLabel(eligible, "Memenuhi syarat", "Tidak memenuhi syarat") }
    ];
    promoEligibilitySimulationConditions(data).forEach((item, index) => rows.push(promoEligibilityDiagnosticCondition(item, index)));
    return rows;
}

function promoEligibilitySimulationChecksMarkup(data = {}) {
    const conditions = promoEligibilitySimulationConditions(data);
    if (!conditions.length) return "";
    const hasEligibilityDiagnostics = !!data?.diagnostics?.eligibility;
    return conditions.map((item, index) => {
        const matched = item.matched ?? item.passed;
        const diagnostic = hasEligibilityDiagnostics ? promoEligibilityDiagnosticCondition(item, index) : null;
        const label = diagnostic?.message || item.label || item.message || item.reason || item.name || `Syarat ${index + 1}`;
        const className = matched === false ? "text-danger" : (matched === true ? "text-success" : "text-muted");
        const icon = matched === false ? "&#10005;" : (matched === true ? "&#10003;" : "&#8226;");
        return `<li class="${className}">${icon} ${escapeHtml(label)}</li>`;
    }).join("");
}

function promoSimulationDiagnosticRows(data = {}, fallbackSegment = "all") {
    const diagnostics = data?.diagnostics || {};
    const identity = diagnostics.identity || {};
    const history = diagnostics.history || {};
    const specific = diagnostics.specific_target || {};
    const segment = diagnostics.customer_segment || data.customer_segment || fallbackSegment;
    const segmentLabel = promoCustomerSegmentDisplay(segment, specific.selected_count || data.customer_ids?.length || 0);
    const historyValue = promoSimulationHistoryLabel(
        history.status,
        history.success_order_count ?? data.success_order_count,
        history.has_success_order,
    );
    const specificValue = specific.required
        ? `${diagnosticBooleanLabel(specific.matched, "Cocok", "Tidak cocok")} • ${Number(specific.selected_count || 0).toLocaleString("id-ID")} target dipilih admin`
        : "Tidak diwajibkan untuk segment ini";
    const legacyRows = [
        { label: "Segmentasi", value: segmentLabel },
        { label: "Kecocokan segment", value: diagnosticBooleanLabel(diagnostics.segment_match, "Cocok", "Tidak cocok") },
        { label: "Status login", value: promoSimulationIdentityStatusLabel(identity.status, identity.is_authenticated) },
        { label: "Status akun", value: diagnosticBooleanLabel(identity.account_active, "Aktif", "Nonaktif", identity.is_authenticated ? "Tidak ditemukan" : "Tidak berlaku untuk guest") },
        { label: "Histori transaksi", value: historyValue },
        { label: "Target khusus", value: specificValue },
        { label: "Kode alasan", value: data.reason_code || "ELIGIBLE" },
    ];
    const eligibilityRows = promoEligibilityDiagnosticRows(data);
    if (!eligibilityRows.length) return legacyRows;
    return [
        ...eligibilityRows,
        ...legacyRows.slice(2, 6),
        { label: "Alasan keputusan", value: String(data.reason || (data.eligible ? "Semua syarat terpenuhi." : "Satu atau lebih syarat belum terpenuhi.")) }
    ];
}

function promoSimulationDiagnosticsMarkup(data, fallbackSegment) {
    const rows = promoSimulationDiagnosticRows(data, fallbackSegment);
    return `<dl class="row g-1 mt-2 mb-0">${rows.map((item) => `<dt class="col-sm-4">${escapeHtml(item.label)}</dt><dd class="col-sm-8 mb-1">${escapeHtml(item.value)}</dd>`).join("")}</dl>`;
}

function populatePromoSimulation(prefix, selectedSku = "", selectedMethod = "") {
    const skuSelect = byId(`${prefix}_simulation_sku`);
    const methodSelect = byId(`${prefix}_simulation_method`);
    if (skuSelect) {
        const skus = promoOptions?.skus || [];
        skuSelect.innerHTML = skus.length
            ? skus.map((item) => {
                const value = item.value || item.sku || "";
                const label = item.label || value;
                return `<option value="${escapeHtml(value)}" ${String(value) === String(selectedSku || "") ? "selected" : ""}>${escapeHtml(label)}</option>`;
            }).join("")
            : `<option value="">Belum ada produk</option>`;
    }
    if (methodSelect) {
        const methods = promoOptions?.payment_methods || [{ value: "QRIS", label: "QRIS" }];
        methodSelect.innerHTML = methods.map((item) => {
            const value = String(item.value || "").toUpperCase();
            return `<option value="${escapeHtml(value)}" ${value === String(selectedMethod || "").toUpperCase() ? "selected" : ""}>${escapeHtml(item.label || value)}</option>`;
        }).join("");
    }
    populatePromoSimulationCustomers(prefix);
    const mode = byId(`${prefix}_simulation_identity_mode`);
    const phone = byId(`${prefix}_simulation_customer_phone`);
    const target = byId(`${prefix}_simulation_target_id`);
    if (mode) mode.value = "guest";
    if (phone) { phone.value = ""; phone.readOnly = false; }
    if (target) target.value = "";
    updatePromoSimulationIdentityFields(prefix);
    const result = byId(`${prefix}_simulation_result`);
    if (result) result.innerHTML = "";
}

function setPromoUsageMode(prefix, limitValue = 0) {
    const limit = Number(limitValue || 0);
    const mode = byId(`${prefix}_usage_mode`);
    const input = byId(`${prefix}_usage_limit`);
    if (!mode || !input) return;
    mode.value = limit > 0 ? "limited" : "unlimited";
    input.disabled = mode.value !== "limited";
    input.value = limit > 0 ? limit : "";
}

function updatePromoUsageLimitState(prefix) {
    const mode = byId(`${prefix}_usage_mode`);
    const input = byId(`${prefix}_usage_limit`);
    if (!mode || !input) return;
    const limited = mode.value === "limited";
    input.disabled = !limited;
    if (!limited) input.value = "";
}

function updatePromoCtaFromPreset(prefix) {
    const preset = byId(`${prefix}_cta_preset`)?.value || "products";
    const urlInput = byId(`${prefix}_cta_url`);
    if (!urlInput || preset === "custom") return;
    if (preset === "products") {
        urlInput.value = "/#produk-section";
    } else if (preset === "latest") {
        urlInput.value = "/promo/latest";
    } else if (preset === "target") {
        const scope = byId(`${prefix}_target_scope`)?.value || "all";
        const target = byId(`${prefix}_target_value`)?.value || "";
        if (scope === "provider" && target) urlInput.value = `game:${target}`;
        else if (scope === "sku" && target) urlInput.value = "/#produk-section";
        else urlInput.value = "/#produk-section";
    }
}

function promoDiscountText(prefix) {
    const type = byId(`${prefix}_discount_type`)?.value || "";
    const value = Number(byId(`${prefix}_discount_value`)?.value || 0);
    const cap = Number(byId(`${prefix}_max_discount`)?.value || 0);
    if (!type || !value) return "promo spesial";
    if (type === "percent") return `diskon ${value.toLocaleString("id-ID")}%${cap ? ` maksimal ${formatCurrency(cap)}` : ""}`;
    return `potongan ${formatCurrency(value)}`;
}

window.autofillPromoDescription = function(prefix = "promo") {
    const title = byId(`${prefix}_title`)?.value || "Promo LIXAFA";
    const code = byId(`${prefix}_code`)?.value || "";
    const scope = byId(`${prefix}_target_scope`)?.value || "all";
    const target = byId(`${prefix}_target_value`)?.value || "";
    const methods = selectedMultiValues(`${prefix}_payment_methods`);
    const usageMode = byId(`${prefix}_usage_mode`)?.value || "unlimited";
    const usageLimit = byId(`${prefix}_usage_limit`)?.value || "";
    const budgetLimit = Number(byId(`${prefix}_budget_limit`)?.value || 0);
    const perPhone = Number(byId(`${prefix}_max_per_phone`)?.value || 0);
    const perTarget = Number(byId(`${prefix}_max_per_target`)?.value || 0);
    const methodText = methods.length ? ` khusus pembayaran ${methods.join(", ")}` : "";
    const targetText = scope === "all" ? "untuk semua produk" : `untuk ${target || scope}`;
    const codeText = code ? ` Gunakan kode ${code.toUpperCase()} saat checkout.` : "";
    const limitText = usageMode === "limited" && usageLimit ? ` Berlaku untuk ${Number(usageLimit).toLocaleString("id-ID")} pemakaian pertama.` : "";
    const budgetText = budgetLimit ? ` Total subsidi promo dibatasi ${formatCurrency(budgetLimit)}.` : "";
    const userLimitText = perPhone || perTarget
        ? ` Maksimal ${perPhone ? `${perPhone}x per nomor WA` : ""}${perPhone && perTarget ? " dan " : ""}${perTarget ? `${perTarget}x per target ID` : ""}.`
        : "";
    const description = `${title}: nikmati ${promoDiscountText(prefix)} ${targetText}${methodText}.${codeText}${limitText}${userLimitText}${budgetText}`;
    byId(`${prefix}_description`).value = description;
    if (byId(`${prefix}_customer_description`)) byId(`${prefix}_customer_description`).value = description;
    renderPromoReview(prefix);
};

window.pickPromoImage = function(prefix = "promo") {
    const input = byId(`${prefix}_image_file`);
    if (!input) return;
    input.value = "";
    input.click();
};

async function uploadPromoImage(prefix = "promo") {
    const input = byId(`${prefix}_image_file`);
    const file = input?.files?.[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    formData.append("folder", "promos");
    try {
        const uploaded = await api("/admin/upload-image", {
            method: "POST",
            body: formData
        });
        byId(`${prefix}_image_url`).value = uploaded.url || "";
        renderPromoReview(prefix);
    } catch (error) {
        showError(error, "Gagal upload gambar promo.");
    }
}

function formatPromoPeriod(promo) {
    return `${formatDate(promo.starts_at)}<br>${formatDate(promo.ends_at)}`;
}

function promoRuntimeBadge(promo) {
    const status = String(promo.runtime_status || "").toLowerCase();
    if (status === "live") return "<span class='badge bg-success'>Aktif</span>";
    if (status === "scheduled") return "<span class='badge bg-warning text-dark'>Akan Datang</span>";
    if (status === "expired") return "<span class='badge bg-danger'>Expired</span>";
    return promo.active ? "<span class='badge bg-success'>Aktif</span>" : "<span class='badge bg-secondary'>Mati</span>";
}

function promoEffectiveType(promo) {
    return canonicalPromoType(promo.promo_type || promo.promotion_type)
        || (promo.rule_type === "content" ? "banner" : (promo.code ? "voucher" : "automatic"));
}

function promoRelationKeys(promo, relationName = "targets") {
    const relation = listValue(promo?.[relationName]);
    return relation
        .map((item) => typeof item === "object" && item !== null
            ? item.target_key ?? item.value ?? item.sku ?? item.label
            : item)
        .map((value) => String(value ?? "").trim())
        .filter(Boolean);
}

function promoEffectiveStatus(promo) {
    const configured = String(promo.lifecycle_status || promo.status || "").toLowerCase();
    if (promo.archived_at || configured === "archived") return "archived";
    if (configured === "draft") return "draft";
    if (["paused", "pause"].includes(configured)) return "paused";
    if (["ended", "end"].includes(configured)) return "expired";
    const usageLimit = Number(promo.usage_limit || promo.quota_total || 0);
    const usageCount = Number(promo.usage_count || promo.redemption_count || 0);
    if (usageLimit > 0 && usageCount >= usageLimit) return "quota_exhausted";
    const budgetLimit = Number(promo.budget_limit || 0);
    const discountSpent = Number(promo.discount_spent || promo.total_discount || 0);
    if (budgetLimit > 0 && discountSpent >= budgetLimit) return "quota_exhausted";
    const runtime = String(promo.runtime_status || "").toLowerCase();
    if (runtime === "scheduled") return "scheduled";
    if (runtime === "expired") return "expired";
    if (runtime === "live") return "live";
    if (!promo.active) return configured === "disabled" ? "disabled" : "draft";
    return "live";
}

function promoStatusMeta(promo) {
    const status = promoEffectiveStatus(promo);
    const map = {
        draft: ["Draft", "bg-secondary"],
        scheduled: ["Dijadwalkan", "bg-warning text-dark"],
        live: ["Aktif", "bg-success"],
        paused: ["Dijeda", "bg-info text-dark"],
        quota_exhausted: ["Kuota Habis", "bg-danger"],
        expired: ["Berakhir", "bg-dark border"],
        disabled: ["Dinonaktifkan", "bg-secondary"],
        archived: ["Diarsipkan", "bg-secondary"]
    };
    const [label, badge] = map[status] || [status || "-", "bg-secondary"];
    return { status, label, badge };
}

function promoTypeLabel(value) {
    return ({ banner: "Konten banner", content: "Konten banner", automatic: "Diskon otomatis", voucher: "Kode voucher", special_price: "Harga khusus", payment_method: "Metode bayar", payment: "Metode bayar", price: "Diskon harga" })[value] || value || "-";
}

function promoDiscountLabel(promo) {
    if (promoEffectiveType(promo) === "banner") return "Tidak mengubah harga";
    if (promo.special_price) return `Harga ${formatCurrency(promo.special_price)}`;
    if (promo.discount_type === "percent") return `${Number(promo.discount_value || 0).toLocaleString("id-ID")}%${promo.max_discount ? `, maks ${formatCurrency(promo.max_discount)}` : ""}`;
    if (promo.discount_type === "fixed") return formatCurrency(promo.discount_value || 0);
    return "Aturan harga";
}

function filteredPromoRows() {
    const search = promoViewState.search.toLowerCase();
    const rows = promoState.filter((promo) => {
        const status = promoEffectiveStatus(promo);
        const type = promoEffectiveType(promo);
        const haystack = [promo.title, promo.code, promo.internal_code, promo.target_scope, promo.target_value, promoEligibilitySummaryFromPromo(promo), ...promoRelationKeys(promo, "targets"), ...listValue(promo.payment_methods)].join(" ").toLowerCase();
        return (!search || haystack.includes(search))
            && (!promoViewState.status || status === promoViewState.status)
            && (!promoViewState.type || type === promoViewState.type || String(promo.rule_type || "") === promoViewState.type)
            && (!promoViewState.scope || String(promo.target_scope || "all") === promoViewState.scope);
    });
    const dateValue = (value) => {
        const parsed = new Date(value || 0).getTime();
        return Number.isFinite(parsed) ? parsed : 0;
    };
    rows.sort((a, b) => {
        if (promoViewState.sort === "updated_asc") return dateValue(a.updated_at || a.created_at) - dateValue(b.updated_at || b.created_at);
        if (promoViewState.sort === "title_asc") return String(a.title || "").localeCompare(String(b.title || ""), "id");
        if (promoViewState.sort === "title_desc") return String(b.title || "").localeCompare(String(a.title || ""), "id");
        if (promoViewState.sort === "priority_desc") return Number(b.priority || 0) - Number(a.priority || 0);
        if (promoViewState.sort === "usage_desc") return Number(b.usage_count || 0) - Number(a.usage_count || 0);
        return dateValue(b.updated_at || b.created_at) - dateValue(a.updated_at || a.created_at);
    });
    return rows;
}

function renderPromoTable() {
    const body = byId("promo_table_body");
    if (!body) return;
    const rows = filteredPromoRows();
    const pageSize = Math.max(1, Number(promoViewState.pageSize || 10));
    const pages = Math.max(1, Math.ceil(rows.length / pageSize));
    promoViewState.page = Math.max(1, Math.min(promoViewState.page, pages));
    const start = (promoViewState.page - 1) * pageSize;
    const visible = rows.slice(start, start + pageSize);
    const summary = byId("promo_result_summary");
    if (summary) summary.textContent = `${rows.length.toLocaleString("id-ID")} dari ${promoState.length.toLocaleString("id-ID")} promo`;
    const pageLabel = byId("promo_page_label");
    if (pageLabel) pageLabel.textContent = `Halaman ${promoViewState.page} dari ${pages}`;
    const prev = byId("promo_page_prev");
    const next = byId("promo_page_next");
    if (prev) prev.disabled = promoViewState.page <= 1;
    if (next) next.disabled = promoViewState.page >= pages;
    if (!visible.length) {
        body.innerHTML = `<tr><td colspan="9" class="text-center text-muted py-5">Tidak ada promo yang sesuai filter.</td></tr>`;
        return;
    }
    body.innerHTML = visible.map((promo) => {
        const id = Number(promo.id || 0);
        const status = promoStatusMeta(promo);
        const type = promoEffectiveType(promo);
        const paymentMethods = listValue(promo.payment_methods);
        const methods = paymentMethods.length ? paymentMethods.join(", ") : "Semua metode";
        const usageCount = Number(promo.usage_count || promo.redemption_count || 0);
        const usageLimit = Number(promo.usage_limit || promo.quota_total || 0);
        const discountSpent = Number(promo.discount_spent || promo.total_discount || 0);
        const structuredTargets = promoRelationKeys(promo, "targets");
        const targetValues = structuredTargets.length ? structuredTargets.join(", ") : (promo.target_value || "Semua");
        const customerEligibility = promoEligibilitySummaryFromPromo(promo);
        return `<tr>
            <td><strong>${escapeHtml(promo.title || "Tanpa nama")}</strong><div class="small text-muted">${escapeHtml(promo.code || promo.internal_code || "Tanpa kode")}</div><div class="small text-warning">${escapeHtml(promoDiscountLabel(promo))}</div></td>
            <td><span class="badge bg-warning text-dark">${escapeHtml(promoTypeLabel(type))}</span><div class="small mt-1">${escapeHtml(promo.target_scope || "all")}: ${escapeHtml(targetValues)}</div><div class="small text-muted promo-table-wrap">${escapeHtml(methods)}</div><div class="small text-primary promo-table-wrap">${escapeHtml(customerEligibility)}</div></td>
            <td class="small">${formatPromoPeriod(promo)}</td>
            <td><strong>${usageCount.toLocaleString("id-ID")}</strong><div class="small text-muted">${usageLimit ? `dari ${usageLimit.toLocaleString("id-ID")}` : "Tanpa batas"}</div></td>
            <td>${formatCurrency(discountSpent)}${promo.budget_limit ? `<div class="small text-muted">Budget ${formatCurrency(promo.budget_limit)}</div>` : ""}</td>
            <td><span class="badge ${status.badge}">${escapeHtml(status.label)}</span></td>
            <td>${promo.show_on_website ? "<span class='badge bg-primary'>Tampil</span>" : "<span class='badge bg-light text-dark'>Hidden</span>"}</td>
            <td class="small">${formatDate(promo.updated_at || promo.created_at)}</td>
            <td class="text-center"><div class="dropdown"><button class="btn btn-sm btn-outline-primary dropdown-toggle" type="button" data-bs-toggle="dropdown" aria-expanded="false" aria-label="Aksi untuk ${escapeHtml(promo.title || "promo")}">Aksi</button><ul class="dropdown-menu dropdown-menu-end promo-action-menu">
                <li><button class="dropdown-item" type="button" data-promo-row-action="detail" data-promo-id="${id}"><i class="bi bi-eye me-2"></i>Lihat detail</button></li>
                <li><button class="dropdown-item" type="button" data-promo-row-action="edit" data-promo-id="${id}"><i class="bi bi-pencil-square me-2"></i>Edit</button></li>
                <li><button class="dropdown-item" type="button" data-promo-row-action="duplicate" data-promo-id="${id}"><i class="bi bi-copy me-2"></i>Duplikat</button></li>
                <li><button class="dropdown-item" type="button" data-promo-row-action="usage" data-promo-id="${id}"><i class="bi bi-graph-up me-2"></i>Lihat penggunaan</button></li>
                <li><hr class="dropdown-divider"></li>
                ${status.status === "live" ? `<li><button class="dropdown-item" type="button" data-promo-row-action="pause" data-promo-id="${id}"><i class="bi bi-pause-circle me-2"></i>Jeda</button></li>` : `<li><button class="dropdown-item" type="button" data-promo-row-action="activate" data-promo-id="${id}"><i class="bi bi-play-circle me-2"></i>Aktifkan</button></li>`}
                <li><button class="dropdown-item" type="button" data-promo-row-action="end" data-promo-id="${id}"><i class="bi bi-stop-circle me-2"></i>Akhiri</button></li>
                <li><button class="dropdown-item text-danger" type="button" data-promo-row-action="archive" data-promo-id="${id}"><i class="bi bi-archive me-2"></i>Arsipkan</button></li>
            </ul></div></td>
        </tr>`;
    }).join("");
}

function promoById(promoId) {
    return promoState.find((item) => Number(item.id) === Number(promoId));
}

window.showPromoDetail = function(promoId) {
    const promo = promoById(promoId);
    if (!promo) return;
    const status = promoStatusMeta(promo);
    const content = byId("promo_detail_content");
    if (!content) return;
    const entries = [
        ["Nama", promo.title], ["Kode internal", promo.internal_code || "-"], ["Kode voucher", promo.code || "-"],
        ["Jenis", promoTypeLabel(promoEffectiveType(promo))], ["Status", status.label], ["Deskripsi", promo.customer_description || promo.description || "-"],
        ["Target", `${promo.target_scope || "all"}: ${promoRelationKeys(promo, "targets").join(", ") || promo.target_value || "Semua"}`],
        ["Syarat pelanggan", promoEligibilitySummaryFromPromo(promo)],
        ["Pengecualian", promoRelationKeys(promo, "exclusions").join(", ") || "-"], ["Metode pembayaran", listValue(promo.payment_methods).join(", ") || "Semua metode"],
        ["Diskon", promoDiscountLabel(promo)], ["Minimum transaksi", formatCurrency(promo.minimum_transaction || 0)],
        ["Pemakaian", `${Number(promo.usage_count || 0).toLocaleString("id-ID")} / ${promo.usage_limit ? Number(promo.usage_limit).toLocaleString("id-ID") : "tanpa batas"}`],
        ["Total diskon", formatCurrency(promo.discount_spent || promo.total_discount || 0)], ["Periode", `${formatDate(promo.starts_at)} - ${formatDate(promo.ends_at)}`],
        ["Penempatan", listValue(promo.placements).join(", ") || (promo.show_on_website ? "Website" : "Tidak ditampilkan")], ["Terakhir diperbarui", formatDate(promo.updated_at || promo.created_at)]
    ];
    content.innerHTML = `<div class="promo-detail-grid">${entries.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}</div>`;
    promoDetailModal?.show();
};

window.showPromoUsage = async function(promoId) {
    const promo = promoById(promoId);
    const content = byId("promo_usage_content");
    if (!promo || !content) return;
    content.innerHTML = `<div class="text-center py-5"><span class="spinner-border spinner-border-sm"></span> Memuat penggunaan...</div>`;
    promoUsageModal?.show();
    try {
        const data = await api(`/admin/api/promos/${promoId}/usage`);
        const rows = data.items || data.usages || data.redemptions || [];
        const summary = data.summary || data;
        content.innerHTML = `<div class="promo-usage-stats"><div><span>Pemakaian</span><strong>${Number(summary.usage_count || summary.total_usage || promo.usage_count || 0).toLocaleString("id-ID")}</strong></div><div><span>Total transaksi</span><strong>${formatCurrency(summary.total_transaction_value || 0)}</strong></div><div><span>Total diskon</span><strong>${formatCurrency(summary.total_discount || promo.discount_spent || 0)}</strong></div><div><span>Rata-rata diskon</span><strong>${formatCurrency(summary.average_discount || 0)}</strong></div></div>${rows.length ? `<div class="table-responsive mt-3"><table class="table"><thead><tr><th>Order</th><th>Pelanggan</th><th>Produk</th><th>Metode</th><th>Diskon</th><th>Status</th><th>Waktu</th></tr></thead><tbody>${rows.map((item) => `<tr><td>${escapeHtml(item.order_id || "-")}</td><td>${escapeHtml(item.customer_identifier || item.phone_masked || "-")}</td><td>${escapeHtml(item.product_name || item.sku || "-")}</td><td>${escapeHtml(item.payment_method || "-")}</td><td>${formatCurrency(item.discount_amount || 0)}</td><td>${escapeHtml(item.status || "-")}</td><td>${formatDate(item.used_at || item.created_at)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="alert alert-info mt-3 mb-0">Belum ada detail penggunaan yang tersedia.</div>`}`;
    } catch (error) {
        content.innerHTML = `<div class="promo-usage-stats"><div><span>Pemakaian tercatat</span><strong>${Number(promo.usage_count || 0).toLocaleString("id-ID")}</strong></div><div><span>Total diskon</span><strong>${formatCurrency(promo.discount_spent || 0)}</strong></div><div><span>Sisa kuota</span><strong>${promo.usage_limit ? Math.max(Number(promo.usage_limit) - Number(promo.usage_count || 0), 0).toLocaleString("id-ID") : "Tanpa batas"}</strong></div></div><div class="alert alert-secondary mt-3 mb-0">Endpoint riwayat rinci belum tersedia. Ringkasan legacy tetap ditampilkan.</div>`;
    }
};

async function fallbackPromoAction(promo, action) {
    const payload = {};
    if (action === "activate") Object.assign(payload, { active: 1, status: "active", lifecycle_status: "active" });
    if (action === "pause") Object.assign(payload, { active: 0, status: "paused", lifecycle_status: "paused" });
    if (action === "end") Object.assign(payload, { active: 0, status: "ended", lifecycle_status: "ended" });
    if (action === "archive") Object.assign(payload, { active: 0, show_on_website: 0, status: "archived", lifecycle_status: "archived" });
    await api(`/admin/api/promos/${promo.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
}

window.performPromoAction = async function(promoId, action) {
    const promo = promoById(promoId);
    if (!promo) return;
    const labels = { activate: "mengaktifkan", pause: "menjeda", end: "mengakhiri", archive: "mengarsipkan" };
    if (!confirm(`Yakin ingin ${labels[action] || action} promo “${promo.title || promo.id}”?`)) return;
    try {
        try {
            await api(`/admin/api/promos/${promo.id}/actions/${action}`, { method: "POST" });
        } catch (error) {
            if (![404, 405, 422].includes(Number(error.status || 0))) throw error;
            await fallbackPromoAction(promo, action);
        }
        await window.loadPromos();
    } catch (error) {
        showError(error, `Gagal ${labels[action] || "memproses"} promo.`);
    }
};

window.duplicatePromo = async function(promoId) {
    const promo = promoById(promoId);
    if (!promo) return;
    if (!confirm(`Duplikat promo “${promo.title || promo.id}” sebagai draft?`)) return;
    try {
        try {
            await api(`/admin/api/promos/${promo.id}/duplicate`, { method: "POST" });
            await window.loadPromos();
        } catch (error) {
            if (![404, 405, 422].includes(Number(error.status || 0))) throw error;
            await window.showAddPromoModal();
            fillPromoForm("promo", { ...promo, id: null, title: `${promo.title || "Promo"} (Salinan)`, code: "", internal_code: "", status: "draft", lifecycle_status: "draft", active: false });
        }
    } catch (error) {
        showError(error, "Gagal menduplikat promo.");
    }
};

function uniqueIntegerArray(values, label) {
    return [...new Set(parseIntegerArray(values, label))];
}

function getPromoPayload(prefix = "promo", options = {}) {
    const usageMode = byId(`${prefix}_usage_mode`)?.value || "unlimited";
    const targetScope = byId(`${prefix}_target_scope`).value;
    const allowExternalCta = !!byId(`${prefix}_allow_external_cta`)?.checked;
    const ctaValidation = validatePromoCtaUrl(byId(`${prefix}_cta_url`)?.value, allowExternalCta);
    if (!ctaValidation.valid && options.validate !== false) throw new Error(ctaValidation.reason);
    const customerDescription = optionalPromoText(byId(`${prefix}_customer_description`)?.value)
        ?? optionalPromoText(byId(`${prefix}_description`)?.value);
    const promoType = canonicalPromoType(byId(`${prefix}_promo_type`)?.value) || inferPromoType({}, prefix);
    const ruleType = promoType === "banner" ? "content" : "price";
    const pricingEnabled = promoType !== "banner";
    const discountType = pricingEnabled && promoType !== "special_price"
        ? (byId(`${prefix}_discount_type`).value || null)
        : null;
    const calculationType = promoType === "special_price" ? "special_price" : discountType;
    const discountValue = discountType
        ? parseOptionalIntegerField(`${prefix}_discount_value`, "discount_value")
        : null;
    const maxDiscount = pricingEnabled && discountType === "percent"
        ? parseOptionalIntegerField(`${prefix}_max_discount`, "max_discount")
        : null;
    const specialPrice = pricingEnabled && promoType === "special_price"
        ? parseOptionalIntegerField(`${prefix}_special_price`, "special_price")
        : null;
    const status = byId(`${prefix}_status`)?.value || (byId(`${prefix}_active`)?.checked ? "active" : "draft");
    const primaryTargetId = targetScope === "all" ? null : selectedPrimaryTargetOptionId(prefix);
    const advancedTargetIds = targetScope === "all" ? [] : selectedMultiValues(`${prefix}_target_values`);
    const targetOptionIds = targetScope === "all"
        ? []
        : uniqueIntegerArray([...(primaryTargetId ? [primaryTargetId] : []), ...advancedTargetIds], "Target Promo");
    const excludedTargetOptionIds = uniqueIntegerArray(selectedMultiValues(`${prefix}_target_exclusions`), "Pengecualian Target");
    const legacyTargetValue = targetScope === "all" ? null : optionalPromoText(byId(`${prefix}_target_value`).value);
    const paymentMethods = [...new Set(parseStringArray(selectedMultiValues(`${prefix}_payment_methods`)).map((value) => value.toUpperCase()))];
    const eligibilityMetadata = promoEligibilityMetadata();
    const eligibilityRules = eligibilityMetadata ? serializePromoEligibilityRules(prefix) : null;
    const eligibilityState = eligibilityMetadata ? promoEligibilityState(prefix) : null;
    const customerSegment = eligibilityMetadata
        ? legacyCustomerSegmentFromPreset(eligibilityState.preset)
        : canonicalPromoCustomerSegment(byId(`${prefix}_customer_segment`)?.value || "all");
    const customerIds = eligibilityMetadata
        ? promoEligibilityRulesCustomerIds(eligibilityRules)
        : (customerSegment === "specific" ? promoCustomerIds(prefix) : []);
    const payload = {
        title: String(byId(`${prefix}_title`).value || "").trim(),
        internal_code: optionalPromoText(byId(`${prefix}_internal_code`)?.value),
        code: promoType === "voucher" ? optionalPromoText(byId(`${prefix}_code`).value) : null,
        description: customerDescription,
        internal_description: optionalPromoText(byId(`${prefix}_internal_description`)?.value),
        customer_description: customerDescription,
        admin_notes: optionalPromoText(byId(`${prefix}_admin_notes`)?.value),
        badge: optionalPromoText(byId(`${prefix}_badge`).value),
        promo_type: promoType,
        lifecycle_status: status,
        rule_type: ruleType,
        target_scope: targetScope,
        target_value: legacyTargetValue,
        target_option_ids: targetOptionIds,
        excluded_target_option_ids: excludedTargetOptionIds,
        discount_type: discountType,
        calculation_type: calculationType,
        discount_value: discountValue,
        max_discount: maxDiscount,
        minimum_transaction: pricingEnabled ? parseOptionalIntegerField(`${prefix}_minimum_transaction`, "minimum_transaction") : null,
        special_price: specialPrice,
        rounding_rule: byId(`${prefix}_rounding_rule`)?.value || "none",
        usage_limit: usageMode === "limited" ? parseOptionalIntegerField(`${prefix}_usage_limit`, "usage_limit") : null,
        quota_daily: parseOptionalIntegerField(`${prefix}_quota_daily`, "quota_daily"),
        payment_methods: paymentMethods,
        budget_limit: parseOptionalIntegerField(`${prefix}_budget_limit`, "budget_limit"),
        max_per_customer: parseOptionalIntegerField(`${prefix}_max_per_customer`, "max_per_customer"),
        max_per_customer_daily: parseOptionalIntegerField(`${prefix}_max_per_customer_daily`, "max_per_customer_daily"),
        max_per_phone: parseOptionalIntegerField(`${prefix}_max_per_phone`, "max_per_phone"),
        max_per_target: parseOptionalIntegerField(`${prefix}_max_per_target`, "max_per_target"),
        customer_segment: customerSegment,
        customer_ids: customerIds,
        stackable: byId(`${prefix}_stackable`)?.value === "0" ? 0 : 1,
        exclusive: byId(`${prefix}_exclusive`)?.value === "1" ? 1 : 0,
        max_promotions_per_order: parseOptionalIntegerField(`${prefix}_max_promotions_per_order`, "max_promotions_per_order"),
        priority: parseOptionalIntegerField(`${prefix}_priority`, "priority"),
        cta_text: optionalPromoText(byId(`${prefix}_cta_text`).value),
        cta_url: ctaValidation.valid ? optionalPromoText(ctaValidation.value) : null,
        allow_external_cta: allowExternalCta ? 1 : 0,
        image_url: optionalPromoText(byId(`${prefix}_image_url`).value),
        starts_at: optionalPromoText(byId(`${prefix}_starts_at`).value),
        ends_at: optionalPromoText(byId(`${prefix}_ends_at`).value),
        timezone: byId(`${prefix}_timezone`)?.value || APP_TIME_ZONE,
        active_days: normalizePromoActiveDays(selectedMultiValues(`${prefix}_active_days`), true),
        daily_start_time: byId(`${prefix}_daily_start`)?.value || null,
        daily_end_time: byId(`${prefix}_daily_end`)?.value || null,
        placements: parseStringArray(selectedMultiValues(`${prefix}_placements`)),
        display_order: parseOptionalIntegerField(`${prefix}_display_order`, "display_order"),
        show_on_website: byId(`${prefix}_show_on_website`).checked ? 1 : 0,
        active: status === "active" ? 1 : 0
    };
    if (eligibilityMetadata) payload.eligibility_rules = eligibilityRules;
    return payload;
}

window.simulatePromo = async function(prefix = "promo") {
    const result = byId(`${prefix}_simulation_result`);
    const sku = byId(`${prefix}_simulation_sku`)?.value || "";
    const method = byId(`${prefix}_simulation_method`)?.value || "QRIS";
    const identityMode = byId(`${prefix}_simulation_identity_mode`)?.value || "guest";
    const identity = buildPromoSimulationIdentity(
        identityMode,
        byId(`${prefix}_simulation_customer_id`)?.value,
        byId(`${prefix}_simulation_customer_phone`)?.value,
        byId(`${prefix}_simulation_target_id`)?.value,
    );
    if (!sku) {
        if (result) result.innerHTML = `<span class="text-danger">Pilih produk simulasi dulu.</span>`;
        return;
    }
    if (identityMode === "member" && !identity.customer_id) {
        if (result) result.innerHTML = `<span class="text-danger">Pilih akun member yang akan disimulasikan.</span>`;
        byId(`${prefix}_simulation_customer_id`)?.focus();
        return;
    }
    if (result) result.innerHTML = `<span class="text-muted">Menghitung simulasi...</span>`;
    try {
        const payload = { ...getPromoPayload(prefix), sku, method, ...identity };
        if (!payload.title) payload.title = "Simulasi Promo";
        const data = await api("/admin/api/promos/simulate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        if (!data.eligible) {
            if (result) {
                const checks = promoEligibilitySimulationChecksMarkup(data);
                result.innerHTML = `<div class="alert alert-warning py-2 mb-0"><strong>Promo tidak berlaku</strong><div>${escapeHtml(data.reason || "Syarat promo belum terpenuhi.")}</div>${checks ? `<ul class="promo-simulation-checks mt-2 mb-0">${checks}</ul>` : ""}${promoSimulationDiagnosticsMarkup(data, payload.customer_segment)}</div>`;
            }
            return;
        }
        const budgetText = data.budget_limit
            ? `<div>Sisa budget: <strong>${formatCurrency(data.budget_remaining || 0)}</strong> dari ${formatCurrency(data.budget_limit || 0)}</div>`
            : "";
        if (result) {
            const checks = promoEligibilitySimulationChecksMarkup(data);
            const marginText = data.estimated_profit !== undefined ? `<div>Estimasi laba: <strong>${formatCurrency(data.estimated_profit || 0)}</strong>${data.estimated_margin_percent !== undefined ? ` (${Number(data.estimated_margin_percent || 0).toLocaleString("id-ID")}% margin)` : ""}</div>` : "";
            const conflicts = (data.conflicts || []).length ? `<div class="mt-2 text-warning"><strong>Konflik:</strong> ${escapeHtml(data.conflicts.map((item) => item.title || item.name || item).join(", "))}</div>` : "";
            result.innerHTML = `
                <div class="alert alert-success py-2 mb-0">
                    <strong>Promo berlaku: YA</strong><div>${escapeHtml(data.reason || "Seluruh syarat terpenuhi.")}</div>
                    ${checks ? `<ul class="promo-simulation-checks my-2">${checks}</ul>` : ""}
                    <div>Harga normal: ${formatCurrency(data.base_price || 0)}</div>
                    <div>Diskon: ${formatCurrency(data.discount_amount || 0)}</div>
                    <div>Harga setelah promo: ${formatCurrency(data.final_price || 0)}</div>
                    <div>Fee pembayaran: ${formatCurrency(data.payment_fee || 0)} <span class="text-muted">(${escapeHtml(data.fee_source || "-")})</span></div>
                    <div>Total bayar user: <strong>${formatCurrency(data.total || 0)}</strong></div>
                    ${marginText}
                    ${budgetText}
                    ${conflicts}
                    ${promoSimulationDiagnosticsMarkup(data, payload.customer_segment)}
                </div>`;
        }
    } catch (error) {
        if (result) result.innerHTML = `<span class="text-danger">${escapeHtml(error.message || "Gagal simulasi promo.")}</span>`;
    }
};

window.loadPromos = async function() {
    if (!ensurePermission("promos:manage")) return;
    hideWorkspace();
    setActiveMenu("nav-promos");
    byId("page-title").innerText = "Manajemen Promo";
    byId("promo_actions").style.display = "block";
    byId("promo_container").style.display = "block";
    const globalSearchRow = document.querySelector(".admin-search-row");
    if (globalSearchRow) globalSearchRow.style.display = "none";

    const body = byId("promo_table_body");
    body.innerHTML = `<tr><td colspan="9" class="text-center">Memuat promo...</td></tr>`;

    try {
        const response = await api("/admin/api/promos");
        promoState = Array.isArray(response) ? response : (response.items || response.promos || []);
        renderPromoTable();
    } catch (error) {
        body.innerHTML = `<tr><td colspan="9" class="text-danger text-center">Gagal memuat promo.</td></tr>`;
        console.error(error);
    }
};

function setPromoCheckboxValues(containerId, values = [], defaultValues = []) {
    const container = byId(containerId);
    if (!container) return;
    const resolvedValues = values === null || values === undefined ? defaultValues : values;
    const normalized = new Set(listValue(resolvedValues).map(String));
    container.querySelectorAll("input[type='checkbox']").forEach((input) => {
        input.checked = normalized.has(String(input.value));
    });
}

function setPromoField(prefix, suffix, value) {
    const field = byId(`${prefix}_${suffix}`);
    if (!field) return;
    field.value = value ?? "";
}

function normalizedPromoFormStatus(promo = {}, isNew = false) {
    const configured = String(promo.lifecycle_status || promo.status || "").trim().toLowerCase();
    const configuredAliases = {
        live: "active",
        scheduled: "active",
        expired: "ended",
        pause: "paused",
        end: "ended"
    };
    const configuredStatus = configuredAliases[configured] || configured;
    if (["draft", "active", "paused", "ended", "disabled", "archived"].includes(configuredStatus)) {
        return configuredStatus;
    }
    if (isNew) return "draft";
    const status = promoEffectiveStatus(promo);
    if (status === "live") return "active";
    if (status === "scheduled") return "active";
    if (status === "expired") return "ended";
    if (status === "quota_exhausted") return promo.active ? "active" : "paused";
    return status || "draft";
}

function fillPromoForm(prefix, source = {}) {
    const promo = source || {};
    const isNew = !promo.id;
    promoEligibilityStates.delete(prefix);
    const defaults = {
        rule_type: "content", target_scope: "all", stackable: true, priority: 0,
        cta_text: "Lihat Promo", cta_url: "/#produk-section", status: "draft",
        show_on_website: true, active: false, timezone: APP_TIME_ZONE,
        customer_segment: "all", rounding_rule: "none", exclusive: 0,
        max_promotions_per_order: 1, display_order: 0,
        placements: ["promo_cards"], active_days: [1, 2, 3, 4, 5, 6, 0]
    };
    const data = { ...defaults, ...promo };
    data.customer_segment = canonicalPromoCustomerSegment(data.customer_segment);
    populatePromoCustomerSegments(prefix, data.customer_segment);
    if (byId(`${prefix}_id`)) setPromoField(prefix, "id", data.id || "");
    ["title", "internal_code", "code", "badge", "internal_description", "admin_notes", "discount_type", "discount_value", "max_discount", "minimum_transaction", "special_price", "rounding_rule", "budget_limit", "max_per_customer", "max_per_customer_daily", "max_per_phone", "max_per_target", "quota_daily", "customer_segment", "priority", "exclusive", "max_promotions_per_order", "cta_text", "cta_url", "image_url", "timezone", "display_order"].forEach((suffix) => {
        setPromoField(prefix, suffix, data[suffix] ?? "");
    });
    setPromoField(prefix, "daily_start", data.daily_start_time || data.daily_start || "");
    setPromoField(prefix, "daily_end", data.daily_end_time || data.daily_end || "");
    const parsedEligibilityRules = parsePromoEligibilityRules(data.eligibility_rules);
    const ruleCustomerIds = promoEligibilityRulesCustomerIds(parsedEligibilityRules);
    const relatedCustomerTargets = listValue(data.customer_ids).length ? data.customer_ids : (data.customer_targets || []);
    const selectedCustomerTargets = parsedEligibilityRules && promoEligibilityHasCustomerCondition(parsedEligibilityRules)
        ? ruleCustomerIds
        : relatedCustomerTargets;
    setPromoCustomerSelections(prefix, selectedCustomerTargets);
    void hydratePromoCustomerSelections(prefix, selectedCustomerTargets);
    populatePromoEligibility(prefix, data);
    const customerDescription = data.customer_description || data.description || "";
    setPromoField(prefix, "description", customerDescription);
    setPromoField(prefix, "customer_description", customerDescription);
    setPromoField(prefix, "promo_type", inferPromoType(data, prefix));
    const formStatus = normalizedPromoFormStatus(promo, isNew);
    setPromoField(prefix, "status", formStatus);
    setPromoField(prefix, "rule_type", data.rule_type || "content");
    setPromoField(prefix, "target_scope", data.target_scope || "all");
    setPromoField(prefix, "payment_method_search", "");
    setPromoField(prefix, "cta_preset", isNew ? "products" : "custom");
    setPromoField(prefix, "starts_at", toDateTimeInput(data.starts_at));
    setPromoField(prefix, "ends_at", toDateTimeInput(data.ends_at));
    setPromoField(prefix, "stackable", data.stackable === false || Number(data.stackable) === 0 ? "0" : "1");
    setPromoField(prefix, "exclusive", data.exclusive ? "1" : "0");
    if (!byId(`${prefix}_max_promotions_per_order`)?.value) setPromoField(prefix, "max_promotions_per_order", data.max_promotions_per_order || 1);
    populatePromoTarget(prefix, data.target_value || "");
    const targetOptionIds = listValue(data.target_option_ids).length
        ? listValue(data.target_option_ids)
        : listValue(data.targets).map((item) => item?.id).filter((value) => value !== null && value !== undefined);
    const excludedTargetOptionIds = listValue(data.excluded_target_option_ids).length
        ? listValue(data.excluded_target_option_ids)
        : listValue(data.exclusions).map((item) => item?.id).filter((value) => value !== null && value !== undefined);
    populatePromoAdvancedTargets(prefix, targetOptionIds, excludedTargetOptionIds);
    populatePromoPaymentMethods(prefix, data.payment_methods || []);
    populatePromoSimulation(prefix, data.target_scope === "sku" ? data.target_value : "", (data.payment_methods || [])[0] || "");
    setPromoUsageMode(prefix, data.usage_limit || data.quota_total || 0);
    const usageHint = byId(`${prefix}_usage_hint`);
    if (usageHint) usageHint.textContent = data.usage_limit ? `Sudah dipakai ${Number(data.usage_count || 0).toLocaleString("id-ID")} kali.` : "";
    const budgetHint = byId(`${prefix}_budget_hint`);
    if (budgetHint) budgetHint.textContent = data.budget_limit ? `Terpakai ${formatCurrency(data.discount_spent || 0)}.` : "";
    const showOnWebsite = byId(`${prefix}_show_on_website`);
    const active = byId(`${prefix}_active`);
    const allowExternal = byId(`${prefix}_allow_external_cta`);
    if (showOnWebsite) showOnWebsite.checked = data.show_on_website !== false && Number(data.show_on_website) !== 0;
    if (active) active.checked = formStatus === "active";
    if (allowExternal) allowExternal.checked = !!data.allow_external_cta || validatePromoCtaUrl(data.cta_url, true).external === true;
    setPromoCheckboxValues(`${prefix}_active_days`, normalizePromoActiveDays(data.active_days || defaults.active_days), defaults.active_days);
    const placements = listValue(data.placements).map((value) => value === "promo_card" ? "promo_cards" : value);
    setPromoCheckboxValues(`${prefix}_placements`, placements, defaults.placements);
    const result = byId(`${prefix}_simulation_result`);
    if (result) result.innerHTML = "";
    setPromoWizardStep(prefix, 1);
    updatePromoConditionalFields(prefix);
    renderPromoReview(prefix);
}

function validatePromoForm(prefix) {
    const feedback = byId(`${prefix}_form_feedback`);
    const title = byId(`${prefix}_title`)?.value.trim();
    const start = byId(`${prefix}_starts_at`)?.value;
    const end = byId(`${prefix}_ends_at`)?.value;
    const promoType = canonicalPromoType(byId(`${prefix}_promo_type`)?.value) || "banner";
    const scope = byId(`${prefix}_target_scope`)?.value || "all";
    const customerSegment = byId(`${prefix}_customer_segment`)?.value || "all";
    const eligibilityEnabled = !!promoEligibilityMetadata();
    const customerSelectionRequired = eligibilityEnabled
        ? promoEligibilityNeedsCustomerSelector(prefix)
        : customerSegment === "specific";
    let message = "";
    let step = 1;
    let invalidField = byId(`${prefix}_title`);
    if (!title) message = "Nama/judul promo wajib diisi.";
    else if (promoType === "voucher" && !byId(`${prefix}_code`)?.value.trim()) {
        message = "Kode voucher pelanggan wajib diisi untuk promo voucher.";
        invalidField = byId(`${prefix}_code`);
    } else if (scope !== "all" && !byId(`${prefix}_target_value`)?.value && !selectedMultiValues(`${prefix}_target_values`).length) {
        message = "Pilih minimal satu target promo.";
        step = 3;
        invalidField = byId(`${prefix}_target_value`);
    } else if (promoType === "payment_method" && !selectedMultiValues(`${prefix}_payment_methods`).length) {
        message = "Pilih minimal satu metode pembayaran untuk promo metode pembayaran.";
        step = 3;
        invalidField = byId(`${prefix}_payment_method_search`);
    } else if (customerSelectionRequired && !promoCustomerIds(prefix).length) {
        message = "Pilih minimal satu akun untuk target Pelanggan tertentu.";
        step = 4;
        invalidField = byId(`${prefix}_customer_search`);
    } else if (start && end && new Date(end).getTime() <= new Date(start).getTime()) {
        message = "Waktu berakhir harus setelah waktu mulai.";
        step = 5;
        invalidField = byId(`${prefix}_ends_at`);
    } else if (!!byId(`${prefix}_daily_start`)?.value !== !!byId(`${prefix}_daily_end`)?.value) {
        message = "Jam aktif harian harus memiliki waktu mulai dan selesai.";
        step = 5;
        invalidField = byId(`${prefix}_daily_start`)?.value ? byId(`${prefix}_daily_end`) : byId(`${prefix}_daily_start`);
    } else if (promoType === "special_price") {
        try {
            const specialPrice = parseOptionalIntegerField(`${prefix}_special_price`, "special_price");
            if (specialPrice === null) message = "Harga Khusus wajib diisi.";
        } catch (error) {
            message = error.message || "Harga Khusus harus berupa angka bulat.";
        }
        if (message) {
            step = 2;
            invalidField = byId(`${prefix}_special_price`);
        }
    } else if (promoType !== "banner") {
        const discountType = byId(`${prefix}_discount_type`)?.value || "";
        if (!discountType) {
            message = "Jenis diskon wajib dipilih untuk promo harga.";
            step = 2;
            invalidField = byId(`${prefix}_discount_type`);
        }
        try {
            if (!message) {
                const discountValue = parseOptionalIntegerField(`${prefix}_discount_value`, "discount_value");
                if (discountValue === null || discountValue <= 0) {
                    message = "Nilai Diskon wajib berupa angka bulat lebih dari 0.";
                } else if (discountType === "percent" && discountValue > 100) {
                    message = "Diskon persentase tidak boleh lebih dari 100%.";
                }
            }
            if (message) {
                step = 2;
                invalidField = byId(`${prefix}_discount_value`);
            }
        } catch (error) {
            message = error.message || "Nilai Diskon harus berupa angka bulat.";
            step = 2;
            invalidField = byId(`${prefix}_discount_value`);
        }
    }
    if (!message) {
        try {
            getPromoPayload(prefix);
        } catch (error) {
            message = error.message || "Data promo belum valid.";
            if (eligibilityEnabled && /pelanggan|customer|akun|autentikasi|syarat|kondisi|aturan|eligibility|transaksi berhasil|nomor whatsapp|target transaksi/i.test(message)) {
                step = 4;
                invalidField = byId(`${prefix}_eligibility_preset`) || invalidField;
            }
            else if (/target|produk|pengecualian|metode/i.test(message)) step = 3;
            else if (/pelanggan|kuota|batas|prioritas/i.test(message)) step = 4;
            else if (/waktu|hari|jam/i.test(message)) step = 5;
            else if (/diskon|harga|transaksi|angka/i.test(message)) step = 2;
        }
    }
    byId(prefix === "promo" ? "addPromoModal" : "editPromoModal")?.querySelectorAll("[aria-invalid='true']").forEach((field) => field.removeAttribute("aria-invalid"));
    if (feedback) {
        feedback.textContent = message;
        feedback.classList.toggle("show", !!message);
    }
    if (message) {
        setPromoWizardStep(prefix, step);
        invalidField?.setAttribute("aria-invalid", "true");
        invalidField?.focus();
    }
    return !message;
}

function setPromoSaving(prefix, saving) {
    const modal = byId(prefix === "promo" ? "addPromoModal" : "editPromoModal");
    const button = modal?.querySelector("[data-promo-save]");
    if (!button) return;
    button.disabled = saving;
    button.innerHTML = saving ? `<span class="spinner-border spinner-border-sm" aria-hidden="true"></span> Menyimpan...` : "Simpan Promo";
}

window.showAddPromoModal = async function() {
    if (!ensurePermission("promos:manage")) return;
    await ensurePromoOptions();
    fillPromoForm("promo", {});
    promoAddModal.show();
};

window.saveNewPromo = async function() {
    if (!ensurePermission("promos:manage") || !validatePromoForm("promo")) return;
    setPromoSaving("promo", true);
    try {
        await api("/admin/api/promos", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(getPromoPayload("promo")) });
        promoAddModal.hide();
        await window.loadPromos();
    } catch (error) {
        const feedback = byId("promo_form_feedback");
        if (feedback) { feedback.textContent = error.message || "Gagal menyimpan promo."; feedback.classList.add("show"); }
        console.error("Gagal menyimpan promo.", error);
    } finally {
        setPromoSaving("promo", false);
    }
};

window.showEditPromoModal = async function(promoId) {
    if (!ensurePermission("promos:manage")) return;
    await ensurePromoOptions();
    const promo = promoById(promoId);
    if (!promo) return;
    fillPromoForm("edit_promo", promo);
    promoEditModal.show();
};

window.saveEditPromo = async function() {
    if (!ensurePermission("promos:manage") || !validatePromoForm("edit_promo")) return;
    const promoId = byId("edit_promo_id").value;
    setPromoSaving("edit_promo", true);
    try {
        await api(`/admin/api/promos/${promoId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(getPromoPayload("edit_promo")) });
        promoEditModal.hide();
        await window.loadPromos();
    } catch (error) {
        const feedback = byId("edit_promo_form_feedback");
        if (feedback) { feedback.textContent = error.message || "Gagal memperbarui promo."; feedback.classList.add("show"); }
        console.error("Gagal memperbarui promo.", error);
    } finally {
        setPromoSaving("edit_promo", false);
    }
};

window.togglePromo = async function(promoId, field) {
    if (!ensurePermission("promos:manage")) return;
    const promo = promoState.find((item) => item.id === promoId);
    if (!promo) return;
    const payload = {};
    payload[field] = promo[field] ? 0 : 1;

    try {
        await api(`/admin/api/promos/${promoId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        window.loadPromos();
    } catch (error) {
        showError(error, "Gagal mengubah promo.");
    }
};

window.deletePromo = async function(promoId) {
    if (!ensurePermission("promos:manage")) return;
    await window.performPromoAction(promoId, "archive");
};

window.loadAdmins = async function() {
    if (!ensurePermission("admins:manage")) return;
    hideWorkspace();
    setActiveMenu("nav-admins");
    byId("page-title").innerText = "Akun Admin & Hak Akses";
    byId("admin_actions").style.display = "block";
    byId("admin_container").style.display = "block";
    byId("admin_table_body").innerHTML = `<tr><td colspan="5" class="text-center">Memuat admin...</td></tr>`;

    try {
        await loadRoleMetadata();
        adminState = await api("/admin/api/admins");
        if (!Array.isArray(adminState) || !adminState.length) {
            byId("admin_table_body").innerHTML = `<tr><td colspan="5" class="text-center text-muted">Belum ada admin.</td></tr>`;
            return;
        }

        byId("admin_table_body").innerHTML = adminState.map((admin) => {
            const selfBadge = currentAdmin && admin.id === currentAdmin.id ? `<span class="badge bg-info text-dark ms-1">Anda</span>` : "";
            return `
                <tr>
                    <td class="fw-bold">${escapeHtml(admin.username)}${selfBadge}</td>
                    <td><span class="badge bg-dark">${escapeHtml(roleLabels[admin.role] || admin.role)}</span></td>
                    <td>${permissionChips(admin.permissions)}</td>
                    <td>${admin.active ? "<span class='badge bg-success'>Aktif</span>" : "<span class='badge bg-secondary'>Nonaktif</span>"}</td>
                    <td class="text-center">
                        <button class="btn btn-sm btn-outline-primary" onclick="showEditAdminModal(${admin.id})"><i class="bi bi-pencil-square"></i></button>
                        <button class="btn btn-sm btn-outline-danger" onclick="deleteAdmin(${admin.id})" ${currentAdmin && admin.id === currentAdmin.id ? "disabled" : ""}><i class="bi bi-trash"></i></button>
                    </td>
                </tr>`;
        }).join("");
    } catch (error) {
        byId("admin_table_body").innerHTML = `<tr><td colspan="5" class="text-danger text-center">Gagal memuat admin.</td></tr>`;
        console.error(error);
    }
};

window.showAddAdminModal = async function() {
    if (!ensurePermission("admins:manage")) return;
    try {
        await loadRoleMetadata();
        byId("admin_username").value = "";
        byId("admin_password").value = "";
        byId("admin_active").checked = true;
        fillRoleSelect("admin_role", "support");
        renderPermissionChecks("admin_permissions", rolePermissions.support || []);
        adminAddModal.show();
    } catch (error) {
        showError(error, "Gagal membuka form admin.");
    }
};

window.saveNewAdmin = async function() {
    if (!ensurePermission("admins:manage")) return;
    const payload = {
        username: byId("admin_username").value.trim(),
        password: byId("admin_password").value,
        role: byId("admin_role").value,
        permissions: collectPermissions("admin_permissions"),
        active: byId("admin_active").checked ? 1 : 0
    };

    try {
        await api("/admin/api/admins", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        adminAddModal.hide();
        window.loadAdmins();
    } catch (error) {
        showError(error, "Gagal membuat admin.");
    }
};

window.showEditAdminModal = function(adminId) {
    if (!ensurePermission("admins:manage")) return;
    const admin = adminState.find((item) => item.id === adminId);
    if (!admin) return;

    byId("edit_admin_id").value = admin.id;
    byId("edit_admin_username").value = admin.username;
    byId("edit_admin_password").value = "";
    byId("edit_admin_active").checked = !!admin.active;
    fillRoleSelect("edit_admin_role", admin.role);
    renderPermissionChecks("edit_admin_permissions", admin.permissions || []);
    adminEditModal.show();
};

window.saveEditAdmin = async function() {
    if (!ensurePermission("admins:manage")) return;
    const adminId = byId("edit_admin_id").value;
    const payload = {
        role: byId("edit_admin_role").value,
        permissions: collectPermissions("edit_admin_permissions"),
        active: byId("edit_admin_active").checked ? 1 : 0
    };
    const password = byId("edit_admin_password").value;
    if (password) payload.password = password;

    try {
        await api(`/admin/api/admins/${adminId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        adminEditModal.hide();
        window.loadAdmins();
    } catch (error) {
        showError(error, "Gagal memperbarui admin.");
    }
};

window.deleteAdmin = async function(adminId) {
    if (!ensurePermission("admins:manage")) return;
    if (!confirm("Hapus akun admin ini?")) return;
    try {
        await api(`/admin/api/admins/${adminId}`, { method: "DELETE" });
        window.loadAdmins();
    } catch (error) {
        showError(error, "Gagal menghapus admin.");
    }
};

function getPagePayload(prefix = "page") {
    const title = byId(`${prefix}_title`).value.trim();
    const slugValue = byId(`${prefix}_slug`).value.trim() || slugify(title);
    return {
        title,
        slug: slugValue,
        excerpt: byId(`${prefix}_excerpt`).value,
        content: byId(`${prefix}_content`).value,
        image_url: byId(`${prefix}_image_url`).value,
        page_type: byId(`${prefix}_type`)?.value || "general",
        badge: byId(`${prefix}_badge`)?.value || "",
        cta_text: byId(`${prefix}_cta_text`)?.value || "",
        cta_url: byId(`${prefix}_cta_url`)?.value || "",
        secondary_cta_text: byId(`${prefix}_secondary_cta_text`)?.value || "",
        secondary_cta_url: byId(`${prefix}_secondary_cta_url`)?.value || "",
        promo_code: byId(`${prefix}_promo_code`)?.value || "",
        highlight_title: byId(`${prefix}_highlight_title`)?.value || "",
        highlight_items: byId(`${prefix}_highlight_items`)?.value || "",
        terms_text: byId(`${prefix}_terms_text`)?.value || "",
        accent_color: byId(`${prefix}_accent_color`)?.value || "gold",
        show_on_website: byId(`${prefix}_show_on_website`).checked ? 1 : 0,
        active: byId(`${prefix}_active`).checked ? 1 : 0
    };
}

window.loadPages = async function() {
    if (!ensurePermission("content:manage")) return;
    hideWorkspace();
    setActiveMenu("nav-pages");
    byId("page-title").innerText = "Konten Website";
    byId("page_actions").style.display = "block";
    byId("page_container").style.display = "block";
    byId("page_table_body").innerHTML = `<tr><td colspan="5" class="text-center">Memuat konten...</td></tr>`;

    try {
        pageState = await api("/admin/api/pages");
        if (!Array.isArray(pageState) || !pageState.length) {
            byId("page_table_body").innerHTML = `<tr><td colspan="5" class="text-center text-muted">Belum ada konten.</td></tr>`;
            return;
        }

        byId("page_table_body").innerHTML = pageState.map((page) => {
            const typeBadge = page.page_type === "promo" ? `<span class="badge bg-warning text-dark ms-1">Promo</span>` : "";
            return `
                <tr>
                    <td><strong>${escapeHtml(page.title)}</strong>${typeBadge}<div class="small text-muted">${escapeHtml(page.excerpt || "-")}</div></td>
                    <td><code>${escapeHtml(page.slug)}</code></td>
                    <td><a href="/p/${encodeURIComponent(page.slug)}" target="_blank" rel="noopener">Buka</a></td>
                    <td>
                        ${page.active ? "<span class='badge bg-success'>Aktif</span>" : "<span class='badge bg-secondary'>Nonaktif</span>"}
                        ${page.show_on_website ? "<span class='badge bg-primary'>Website</span>" : "<span class='badge bg-light text-dark'>Hidden</span>"}
                    </td>
                    <td class="text-center">
                        <button class="btn btn-sm btn-outline-primary" onclick="showEditPageModal(${page.id})"><i class="bi bi-pencil-square"></i></button>
                        <button class="btn btn-sm btn-outline-danger" onclick="deletePage(${page.id})"><i class="bi bi-trash"></i></button>
                    </td>
                </tr>`;
        }).join("");
    } catch (error) {
        byId("page_table_body").innerHTML = `<tr><td colspan="5" class="text-danger text-center">Gagal memuat konten.</td></tr>`;
        console.error(error);
    }
};

window.showAddPageModal = function() {
    if (!ensurePermission("content:manage")) return;
    [
        "page_title", "page_slug", "page_excerpt", "page_image_url", "page_content",
        "page_badge", "page_cta_text", "page_cta_url", "page_secondary_cta_text",
        "page_secondary_cta_url", "page_promo_code", "page_highlight_title",
        "page_highlight_items", "page_terms_text"
    ].forEach((id) => {
        byId(id).value = "";
    });
    byId("page_type").value = "promo";
    byId("page_accent_color").value = "gold";
    byId("page_cta_text").value = "Ambil Promo";
    byId("page_cta_url").value = "/#produk-section";
    byId("page_secondary_cta_text").value = "Lihat Produk";
    byId("page_secondary_cta_url").value = "/#produk-section";
    byId("page_show_on_website").checked = true;
    byId("page_active").checked = true;
    pageAddModal.show();
};

window.saveNewPage = async function() {
    if (!ensurePermission("content:manage")) return;
    try {
        await api("/admin/api/pages", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(getPagePayload("page"))
        });
        pageAddModal.hide();
        window.loadPages();
    } catch (error) {
        showError(error, "Gagal menyimpan konten.");
    }
};

window.showEditPageModal = function(pageId) {
    if (!ensurePermission("content:manage")) return;
    const page = pageState.find((item) => item.id === pageId);
    if (!page) return;

    byId("edit_page_id").value = page.id;
    byId("edit_page_title").value = page.title || "";
    byId("edit_page_slug").value = page.slug || "";
    byId("edit_page_excerpt").value = page.excerpt || "";
    byId("edit_page_image_url").value = page.image_url || "";
    byId("edit_page_content").value = page.content || "";
    byId("edit_page_type").value = page.page_type || "general";
    byId("edit_page_badge").value = page.badge || "";
    byId("edit_page_cta_text").value = page.cta_text || "";
    byId("edit_page_cta_url").value = page.cta_url || "";
    byId("edit_page_secondary_cta_text").value = page.secondary_cta_text || "";
    byId("edit_page_secondary_cta_url").value = page.secondary_cta_url || "";
    byId("edit_page_promo_code").value = page.promo_code || "";
    byId("edit_page_highlight_title").value = page.highlight_title || "";
    byId("edit_page_highlight_items").value = page.highlight_items || "";
    byId("edit_page_terms_text").value = page.terms_text || "";
    byId("edit_page_accent_color").value = page.accent_color || "gold";
    byId("edit_page_show_on_website").checked = !!page.show_on_website;
    byId("edit_page_active").checked = !!page.active;
    pageEditModal.show();
};

window.saveEditPage = async function() {
    if (!ensurePermission("content:manage")) return;
    const pageId = byId("edit_page_id").value;
    try {
        await api(`/admin/api/pages/${pageId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(getPagePayload("edit_page"))
        });
        pageEditModal.hide();
        window.loadPages();
    } catch (error) {
        showError(error, "Gagal memperbarui konten.");
    }
};

window.deletePage = async function(pageId) {
    if (!ensurePermission("content:manage")) return;
    if (!confirm("Hapus konten ini?")) return;
    try {
        await api(`/admin/api/pages/${pageId}`, { method: "DELETE" });
        window.loadPages();
    } catch (error) {
        showError(error, "Gagal menghapus konten.");
    }
};

function renderReportCards(summary = {}) {
    const cards = [
        ["Order", summary.orders || 0, "bi-receipt"],
        ["Paid", summary.paid_orders || 0, "bi-check2-circle"],
        ["Success", summary.success_orders || 0, "bi-lightning-charge"],
        ["Failed", summary.failed_orders || 0, "bi-x-circle"],
        ["Revenue", formatCurrency(summary.revenue || 0), "bi-wallet2"],
        ["Profit", formatCurrency(summary.realized_profit || 0), "bi-graph-up-arrow"],
        ["Modal", formatCurrency(summary.product_cost || 0), "bi-box"],
        ["Refund", formatCurrency(summary.refund_amount || 0), "bi-arrow-counterclockwise"]
    ];
    return `
        <div class="row g-3 mb-4">
            ${cards.map(([label, value, icon]) => `
                <div class="col-6 col-md-3">
                    <div class="stat-card p-3 h-100">
                        <p class="text-muted mb-1 fw-semibold"><i class="bi ${icon} me-1"></i> ${label}</p>
                        <h5 class="fw-bold text-dark mb-0">${value}</h5>
                    </div>
                </div>`).join("")}
        </div>`;
}

window.loadReports = async function() {
    if (!ensurePermission("finance:view")) return;
    hideWorkspace();
    setActiveMenu("nav-reports");
    byId("page-title").innerText = "Laporan Finance";
    byId("report_container").style.display = "block";
    const content = byId("report_content");
    content.innerHTML = `<div class="text-center p-5"><div class="spinner-border text-warning"></div><br>Memuat laporan...</div>`;

    const dateFrom = byId("report_date_from")?.value || "";
    const dateTo = byId("report_date_to")?.value || "";
    const params = new URLSearchParams();
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);

    try {
        const data = await api(`/admin/api/reports/finance${params.toString() ? `?${params.toString()}` : ""}`);
        const summary = data.summary || {};
        const products = summary.top_products || [];
        const productRows = products.length ? products.map((product) => `
            <tr>
                <td><code>${escapeHtml(product.sku)}</code></td>
                <td>${escapeHtml(product.name || "-")}</td>
                <td>${escapeHtml(product.provider || "-")}</td>
                <td>${Number(product.orders || 0).toLocaleString("id-ID")}</td>
                <td>${formatCurrency(product.revenue || 0)}</td>
                <td>${formatCurrency(product.profit || 0)}</td>
            </tr>`).join("") : `<tr><td colspan="6" class="text-center text-muted py-4">Belum ada produk pada periode ini.</td></tr>`;

        const orderRows = (data.orders || []).slice(0, 40).map((order) => `
            <tr>
                <td><code>${escapeHtml(String(order.id || "").slice(0, 10))}</code></td>
                <td>${escapeHtml(order.phone || "-")}</td>
                <td>${escapeHtml(order.product_name || order.nominal || "-")}</td>
                <td>${formatCurrency(order.amount || 0)}</td>
                <td>${formatCurrency(order.realized_profit || 0)}</td>
                <td><span class="badge ${paymentBadge(order.payment_status)}">${escapeHtml(order.payment_status || "-")}</span></td>
                <td><span class="badge ${topupBadge(order.topup_status)}">${escapeHtml(order.topup_status || "-")}</span></td>
                <td>${formatDate(order.created_at)}</td>
            </tr>`).join("");

        content.innerHTML = `
            <div class="dashboard-panel p-3 mb-3">
                <div class="row g-2 align-items-end">
                    <div class="col-md-3">
                        <label class="form-label fw-bold">Dari</label>
                        <input type="date" id="report_date_from" class="form-control" value="${escapeHtml(dateFrom)}">
                    </div>
                    <div class="col-md-3">
                        <label class="form-label fw-bold">Sampai</label>
                        <input type="date" id="report_date_to" class="form-control" value="${escapeHtml(dateTo)}">
                    </div>
                    <div class="col-md-6 d-flex gap-2 justify-content-md-end">
                        <button class="btn btn-outline-primary" onclick="loadReports()"><i class="bi bi-funnel"></i> Terapkan</button>
                        <button class="btn btn-success" onclick="downloadReportCsv()"><i class="bi bi-download"></i> Export CSV</button>
                    </div>
                </div>
            </div>
            ${renderReportCards(summary)}
            <div class="table-custom p-3 mb-4">
                <h6 class="fw-bold mb-3">Produk Teratas</h6>
                <div class="table-responsive">
                    <table class="table table-hover align-middle mb-0">
                        <thead class="table-light"><tr><th>SKU</th><th>Produk</th><th>Provider</th><th>Order</th><th>Revenue</th><th>Profit</th></tr></thead>
                        <tbody>${productRows}</tbody>
                    </table>
                </div>
            </div>
            <div class="table-custom p-3">
                <h6 class="fw-bold mb-3">Transaksi Periode Ini</h6>
                <div class="table-responsive">
                    <table class="table table-hover align-middle mb-0">
                        <thead class="table-light"><tr><th>ID</th><th>HP</th><th>Produk</th><th>Total</th><th>Profit</th><th>Payment</th><th>Topup</th><th>Waktu</th></tr></thead>
                        <tbody>${orderRows || `<tr><td colspan="8" class="text-center text-muted py-4">Belum ada transaksi.</td></tr>`}</tbody>
                    </table>
                </div>
            </div>`;
    } catch (error) {
        content.innerHTML = `<div class="alert alert-danger">Gagal memuat laporan finance.</div>`;
        showError(error, "Gagal memuat laporan finance.");
    }
};

window.downloadReportCsv = async function() {
    if (!ensurePermission("finance:view")) return;
    const dateFrom = byId("report_date_from")?.value || "";
    const dateTo = byId("report_date_to")?.value || "";
    const params = new URLSearchParams();
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    const url = `/admin/api/reports/export${params.toString() ? `?${params.toString()}` : ""}`;

    try {
        const res = await fetch(url, { headers: { token } });
        if (!res.ok) throw new Error("Export CSV gagal.");
        const blob = await res.blob();
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = "lixafa-transactions.csv";
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(link.href);
    } catch (error) {
        showError(error, "Gagal export CSV.");
    }
};

window.loadCustomers = async function() {
    if (!ensurePermission("customers:manage")) return;
    hideWorkspace();
    setActiveMenu("nav-customers");
    byId("page-title").innerText = "Customer & Blacklist";
    byId("customer_container").style.display = "block";
    const content = byId("customer_content");
    content.innerHTML = `<div class="text-center p-5"><div class="spinner-border text-warning"></div><br>Memuat customer...</div>`;

    try {
        const customers = await api("/admin/api/customers");
        const rows = customers.length ? customers.map((customer) => `
            <tr>
                <td>
                    <div class="fw-bold">${escapeHtml(customer.customer_name || customer.phone || "-")}</div>
                    <small class="text-muted">${escapeHtml(customer.phone || "-")}${customer.customer_email ? ` • ${escapeHtml(customer.customer_email)}` : ""}</small>
                </td>
                <td>${Number(customer.order_count || 0).toLocaleString("id-ID")}</td>
                <td>${Number(customer.success_count || 0).toLocaleString("id-ID")}</td>
                <td>${Number(customer.failed_count || 0).toLocaleString("id-ID")}</td>
                <td>${formatCurrency(customer.total_spend || 0)}</td>
                <td>${formatCurrency(customer.wallet_balance || 0)}</td>
                <td>${customer.blocked ? `<span class="badge bg-danger">Blacklist</span><br><small>${escapeHtml(customer.block_reason)}</small>` : `<span class="badge bg-success">Aman</span>`}</td>
                <td>${formatDate(customer.last_order_at)}</td>
                <td class="text-end">
                    ${customer.customer_id ? `<button class="btn btn-sm btn-outline-primary" onclick="adjustWallet('${escapeJs(customer.phone)}')"><i class="bi bi-wallet2"></i></button>` : ""}
                    ${customer.blocked
                        ? `<button class="btn btn-sm btn-outline-success" onclick="unblockCustomer('${escapeJs(customer.phone)}')"><i class="bi bi-unlock"></i></button>`
                        : `<button class="btn btn-sm btn-outline-danger" onclick="blockCustomer('${escapeJs(customer.phone)}')"><i class="bi bi-shield-x"></i></button>`}
                </td>
            </tr>`).join("") : `<tr><td colspan="9" class="text-center text-muted py-4">Belum ada customer.</td></tr>`;

        content.innerHTML = `
            <div class="table-custom p-3">
                <div class="table-responsive">
                    <table class="table table-hover align-middle mb-0">
                        <thead class="table-light"><tr><th>Customer</th><th>Order</th><th>Success</th><th>Failed</th><th>Total Belanja</th><th>Wallet</th><th>Status</th><th>Terakhir</th><th class="text-end">Aksi</th></tr></thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
            </div>
            <div class="table-custom p-3 mt-4">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h6 class="fw-bold mb-0">Ticket Support</h6>
                    <button class="btn btn-sm btn-outline-primary" onclick="loadCustomers()"><i class="bi bi-arrow-clockwise"></i> Refresh</button>
                </div>
                <div class="table-responsive">
                    <table class="table table-hover align-middle mb-0">
                        <thead class="table-light"><tr><th>Ticket</th><th>Customer</th><th>Order</th><th>Status</th><th>Prioritas</th><th>Waktu</th><th class="text-end">Aksi</th></tr></thead>
                        <tbody id="support_ticket_rows"><tr><td colspan="7" class="text-center text-muted py-4">Memuat ticket...</td></tr></tbody>
                    </table>
                </div>
            </div>`;
        await loadSupportTickets();
    } catch (error) {
        content.innerHTML = `<div class="alert alert-danger">Gagal memuat customer.</div>`;
        showError(error, "Gagal memuat customer.");
    }
};

window.blockCustomer = async function(phone) {
    if (!ensurePermission("customers:manage")) return;
    const reason = prompt("Alasan blacklist customer:", "Aktivitas transaksi mencurigakan");
    if (!reason) return;
    try {
        await api("/admin/api/customers/block", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ phone, reason })
        });
        window.loadCustomers();
    } catch (error) {
        showError(error, "Gagal blacklist customer.");
    }
};

window.unblockCustomer = async function(phone) {
    if (!ensurePermission("customers:manage")) return;
    if (!confirm("Buka blacklist customer ini?")) return;
    try {
        await api(`/admin/api/customers/block/${encodeURIComponent(phone)}`, { method: "DELETE" });
        window.loadCustomers();
    } catch (error) {
        showError(error, "Gagal membuka blacklist customer.");
    }
};

window.adjustWallet = async function(phone) {
    if (!ensurePermission("customers:manage")) return;
    const amountText = prompt("Nominal adjustment wallet:", "10000");
    if (!amountText) return;
    const amount = Number(amountText);
    if (!amount || amount <= 0) {
        alert("Nominal tidak valid.");
        return;
    }
    const entryType = prompt("Tipe adjustment: CREDIT atau DEBIT", "CREDIT");
    if (!entryType) return;
    const note = prompt("Catatan adjustment:", "Topup saldo manual");
    try {
        await api("/admin/api/customers/wallet-adjust", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ phone, amount, entry_type: entryType.toUpperCase(), note })
        });
        window.loadCustomers();
    } catch (error) {
        showError(error, "Gagal adjustment wallet.");
    }
};

async function loadSupportTickets() {
    if (!hasPermission("customers:manage")) return;
    const tbody = byId("support_ticket_rows");
    if (!tbody) return;
    try {
        const tickets = await api("/admin/api/support-tickets");
        tbody.innerHTML = tickets.length ? tickets.map((ticket) => `
            <tr>
                <td>
                    <div class="fw-bold">#${Number(ticket.id || 0).toLocaleString("id-ID")}</div>
                    <small class="text-muted">${escapeHtml(ticket.subject || "-")}</small>
                </td>
                <td>
                    <div>${escapeHtml(ticket.name || ticket.phone || "-")}</div>
                    <small class="text-muted">${escapeHtml(ticket.phone || ticket.email || "-")}</small>
                </td>
                <td><code>${escapeHtml(ticket.order_id || "-")}</code></td>
                <td><span class="badge ${ticket.status === "RESOLVED" || ticket.status === "CLOSED" ? "bg-success" : "bg-warning text-dark"}">${escapeHtml(ticket.status || "-")}</span></td>
                <td>${escapeHtml(ticket.priority || "-")}</td>
                <td>${formatDate(ticket.created_at)}</td>
                <td class="text-end">
                    <button class="btn btn-sm btn-outline-primary" onclick="updateSupportTicket(${Number(ticket.id)}, '${escapeJs(ticket.status || "OPEN")}')"><i class="bi bi-pencil-square"></i></button>
                </td>
            </tr>`).join("") : `<tr><td colspan="7" class="text-center text-muted py-4">Belum ada ticket support.</td></tr>`;
    } catch (error) {
        tbody.innerHTML = `<tr><td colspan="7" class="text-danger text-center py-4">Gagal memuat ticket.</td></tr>`;
    }
}

window.updateSupportTicket = async function(ticketId, currentStatus) {
    if (!ensurePermission("customers:manage")) return;
    const status = prompt("Status ticket: OPEN, IN_PROGRESS, RESOLVED, CLOSED", currentStatus || "OPEN");
    if (!status) return;
    const adminNote = prompt("Catatan admin:", "");
    try {
        await api(`/admin/api/support-tickets/${ticketId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: status.toUpperCase(), admin_note: adminNote || "" })
        });
        await loadSupportTickets();
    } catch (error) {
        showError(error, "Gagal update ticket.");
    }
};

window.loadAuditLogs = async function() {
    if (!ensurePermission("audit:view")) return;
    hideWorkspace();
    setActiveMenu("nav-audit");
    byId("page-title").innerText = "Audit & Webhook Log";
    byId("audit_container").style.display = "block";
    const content = byId("audit_content");
    content.innerHTML = `<div class="text-center p-5"><div class="spinner-border text-warning"></div><br>Memuat audit log...</div>`;

    try {
        const [audits, webhooks, notifications] = await Promise.all([
            api("/admin/api/audit-logs"),
            hasPermission("api:monitor") ? api("/admin/api/webhook-events") : Promise.resolve([]),
            api("/admin/api/notification-outbox")
        ]);

        const auditRows = audits.length ? audits.map((log) => `
            <tr>
                <td>${formatDate(log.created_at)}</td>
                <td>${escapeHtml(log.actor || "-")}</td>
                <td><span class="badge bg-dark">${escapeHtml(log.action || "-")}</span></td>
                <td>${escapeHtml(log.entity_type || "-")}</td>
                <td><code>${escapeHtml(log.entity_id || "-")}</code></td>
            </tr>`).join("") : `<tr><td colspan="5" class="text-center text-muted py-4">Belum ada audit log.</td></tr>`;

        const webhookRows = webhooks.length ? webhooks.map((event) => `
            <tr>
                <td>${formatDate(event.created_at)}</td>
                <td>${escapeHtml(event.provider || "-")}</td>
                <td>${escapeHtml(event.event_type || "-")}</td>
                <td><code>${escapeHtml(event.reference_id || "-")}</code></td>
                <td><span class="badge ${event.signature_valid ? "bg-success" : "bg-danger"}">${event.signature_valid ? "Valid" : "Invalid"}</span></td>
                <td>${escapeHtml(event.response_status || "-")}</td>
                <td class="small">${escapeHtml(event.message || "-")}</td>
            </tr>`).join("") : `<tr><td colspan="7" class="text-center text-muted py-4">Belum ada webhook log.</td></tr>`;

        const notificationRows = notifications.length ? notifications.map((item) => `
            <tr>
                <td>${formatDate(item.created_at)}</td>
                <td>${escapeHtml(item.channel || "-")}</td>
                <td>${escapeHtml(item.recipient || "-")}</td>
                <td>${escapeHtml(item.subject || "-")}</td>
                <td><span class="badge ${item.status === "SENT" ? "bg-success" : "bg-warning text-dark"}">${escapeHtml(item.status || "-")}</span></td>
                <td>${escapeHtml(item.reference_type || "-")}</td>
                <td><code>${escapeHtml(item.reference_id || "-")}</code></td>
            </tr>`).join("") : `<tr><td colspan="7" class="text-center text-muted py-4">Belum ada notification outbox.</td></tr>`;

        content.innerHTML = `
            <div class="table-custom p-3 mb-4">
                <h6 class="fw-bold mb-3">Audit Admin</h6>
                <div class="table-responsive">
                    <table class="table table-hover align-middle mb-0">
                        <thead class="table-light"><tr><th>Waktu</th><th>Admin</th><th>Aksi</th><th>Entity</th><th>ID</th></tr></thead>
                        <tbody>${auditRows}</tbody>
                    </table>
                </div>
            </div>
            <div class="table-custom p-3">
                <h6 class="fw-bold mb-3">Webhook Provider</h6>
                <div class="table-responsive">
                    <table class="table table-hover align-middle mb-0">
                        <thead class="table-light"><tr><th>Waktu</th><th>Provider</th><th>Event</th><th>Ref</th><th>Signature</th><th>Status</th><th>Pesan</th></tr></thead>
                        <tbody>${webhookRows}</tbody>
                    </table>
                </div>
            </div>
            <div class="table-custom p-3 mt-4">
                <h6 class="fw-bold mb-3">Notification Outbox</h6>
                <div class="table-responsive">
                    <table class="table table-hover align-middle mb-0">
                        <thead class="table-light"><tr><th>Waktu</th><th>Channel</th><th>Tujuan</th><th>Subjek</th><th>Status</th><th>Ref Type</th><th>Ref ID</th></tr></thead>
                        <tbody>${notificationRows}</tbody>
                    </table>
                </div>
            </div>`;
    } catch (error) {
        content.innerHTML = `<div class="alert alert-danger">Gagal memuat audit log.</div>`;
        showError(error, "Gagal memuat audit log.");
    }
};

function renderConfigRows(items = []) {
    if (!items.length) {
        return `<div class="text-muted small">Tidak ada data konfigurasi.</div>`;
    }

    return items.map((item) => {
        const ready = !!item.configured;
        return `
            <div class="config-row">
                <span>${escapeHtml(item.label)}</span>
                <span class="d-inline-flex align-items-center gap-2">
                    ${item.masked ? `<code class="small">${escapeHtml(item.masked)}</code>` : ""}
                    <span class="badge ${ready ? "bg-success" : "bg-danger"}">${ready ? "Terpasang" : "Belum"}</span>
                </span>
            </div>`;
    }).join("");
}

function renderProviderCard(provider, icon) {
    const configured = !!provider.configured;
    return `
        <div class="api-status-card">
            <div class="api-status-title">
                <div>
                    <div class="fw-bold"><i class="bi ${icon} me-1"></i> ${escapeHtml(provider.name)}</div>
                    <div class="text-muted small">${escapeHtml(provider.base_url || "-")}</div>
                </div>
                <span class="status-dot ${configured ? "ok" : ""}" title="${configured ? "Aktif" : "Belum lengkap"}"></span>
            </div>
            ${renderConfigRows(provider.credentials || [])}
            ${provider.username ? `<div class="config-row"><span>Username</span><strong>${escapeHtml(provider.username)}</strong></div>` : ""}
            ${provider.mode ? `<div class="config-row"><span>Mode</span><strong>${escapeHtml(provider.mode)}</strong></div>` : ""}
            ${provider.callback_url ? `<div class="config-row"><span>Callback</span><code class="small">${escapeHtml(provider.callback_url)}</code></div>` : ""}
        </div>`;
}

function renderProviderOptions(values = [], selectedValue = "", includeEmpty = false) {
    const options = includeEmpty ? [`<option value="">Tidak ada</option>`] : [];
    values.forEach((value) => {
        const label = value === "tripay" ? "Tripay" : (value === "digiflazz" ? "Digiflazz" : value);
        options.push(`<option value="${escapeHtml(value)}" ${value === selectedValue ? "selected" : ""}>${escapeHtml(label)}</option>`);
    });
    return options.join("");
}

function renderApiSettings(data) {
    const providers = data.providers || {};
    const supportedPayments = data.supported_payment_providers || ["tripay"];
    const supportedTopups = data.supported_topup_providers || ["digiflazz"];
    const tripay = providers.tripay || {};
    const digiflazz = providers.digiflazz || {};

    return `
        <form class="api-settings-grid mb-4" onsubmit="saveProviderSettings(event)">
            <div class="api-settings-card">
                <div class="d-flex justify-content-between align-items-start gap-3 mb-3">
                    <div>
                        <h6 class="fw-bold mb-1"><i class="bi bi-shield-lock me-1 text-warning"></i> Routing Provider</h6>
                        <span class="secret-hint">Owner only</span>
                    </div>
                </div>
                <div class="row">
                    <div class="col-md-6 mb-3">
                        <label class="form-label fw-bold">Pembayaran Aktif</label>
                        <select id="api_active_payment_provider" class="form-select">
                            ${renderProviderOptions(supportedPayments, data.active_payment_provider || "tripay")}
                        </select>
                    </div>
                    <div class="col-md-6 mb-3">
                        <label class="form-label fw-bold">Topup Aktif</label>
                        <select id="api_active_topup_provider" class="form-select">
                            ${renderProviderOptions(supportedTopups, data.active_topup_provider || "digiflazz")}
                        </select>
                    </div>
                    <div class="col-md-6 mb-3">
                        <label class="form-label fw-bold">Topup Cadangan</label>
                        <select id="api_backup_topup_provider" class="form-select">
                            ${renderProviderOptions(supportedTopups, data.backup_topup_provider || "", true)}
                        </select>
                    </div>
                    <div class="col-md-6 mb-3 d-flex align-items-end">
                        <div class="form-check">
                            <input class="form-check-input" type="checkbox" id="api_topup_failover_enabled" ${data.topup_failover_enabled ? "checked" : ""}>
                            <label class="form-check-label fw-bold" for="api_topup_failover_enabled">Failover Topup</label>
                        </div>
                    </div>
                    <div class="col-md-6 mb-3">
                        <label class="form-label fw-bold">Expired Invoice (menit)</label>
                        <input type="number" id="api_tripay_invoice_expiry_minutes" min="5" max="1440" class="form-control" value="${Number(data.tripay_invoice_expiry_minutes || 30)}">
                    </div>
                    <div class="col-md-6 mb-3">
                        <label class="form-label fw-bold">Batas Saldo Rendah Digiflazz</label>
                        <input type="number" id="api_digiflazz_low_balance_threshold" min="0" class="form-control" value="${Number(data.digiflazz_low_balance_threshold || 50000)}">
                    </div>
                </div>
            </div>

            <div class="api-settings-card">
                <h6 class="fw-bold mb-3"><i class="bi bi-credit-card me-1 text-warning"></i> Tripay</h6>
                <div class="mb-3">
                    <label class="form-label fw-bold">Base URL</label>
                    <input type="text" id="api_tripay_base_url" class="form-control" value="${escapeHtml(tripay.base_url || "")}">
                </div>
                <p class="small text-muted mb-0">Kredensial Tripay dikelola melalui environment variables dan tidak disimpan dari dashboard.</p>
            </div>

            <div class="api-settings-card">
                <h6 class="fw-bold mb-3"><i class="bi bi-lightning-charge me-1 text-warning"></i> Digiflazz</h6>
                <div class="mb-3">
                    <label class="form-label fw-bold">Base URL</label>
                    <input type="text" id="api_digiflazz_base_url" class="form-control" value="${escapeHtml(digiflazz.base_url || "")}">
                </div>
                <div class="row">
                    <div class="col-md-6 mb-3">
                        <label class="form-label fw-bold">Username</label>
                        <input type="text" id="api_digiflazz_username" class="form-control" value="${escapeHtml(digiflazz.username || "")}">
                    </div>
                    <div class="col-md-6 mb-3">
                        <label class="form-label fw-bold">Max Price Margin (%)</label>
                        <input type="number" id="api_digiflazz_max_price_margin_percent" min="0" max="100" class="form-control" value="${Number(data.digiflazz_max_price_margin_percent || 10)}">
                    </div>
                    <div class="col-md-6 mb-3 d-flex align-items-end">
                        <div class="form-check">
                            <input class="form-check-input" type="checkbox" id="api_digiflazz_testing_enabled" ${data.digiflazz_testing_enabled ? "checked" : ""}>
                            <label class="form-check-label fw-bold" for="api_digiflazz_testing_enabled">Mode Testing Digiflazz</label>
                        </div>
                    </div>
                </div>
                <p class="small text-muted mb-0">API key dan webhook secret Digiflazz dikelola melalui environment variables dan tidak disimpan dari dashboard.</p>
            </div>

            <div class="api-settings-card d-flex flex-column justify-content-between">
                <div>
                    <h6 class="fw-bold mb-3"><i class="bi bi-diagram-3 me-1 text-warning"></i> Endpoint</h6>
                    <div class="config-row"><span>Callback Tripay</span><code class="small">${escapeHtml(data.callback_url || "-")}</code></div>
                    <div class="config-row"><span>Environment</span><strong>${escapeHtml(data.app_env || "-")}</strong></div>
                </div>
                <div class="text-end mt-3">
                    <button class="btn btn-gold px-4" type="submit"><i class="bi bi-save me-1"></i> Simpan API</button>
                </div>
            </div>
        </form>`;
}

function renderFlowSteps(flow = []) {
    if (!flow.length) {
        return `<div class="text-muted small">Belum ada data alur transaksi.</div>`;
    }

    return `
        <div class="api-flow">
            ${flow.map((step) => `
                <div class="api-flow-step">
                    <strong>${escapeHtml(step.title)}</strong>
                    <span>${escapeHtml(step.description)}</span>
                </div>`).join("")}
        </div>`;
}

function renderMetricCards(metrics = []) {
    if (!metrics.length) return "";
    return `
        <div class="row g-3 mb-4">
            ${metrics.map((metric) => `
                <div class="col-6 col-md-3">
                    <div class="stat-card p-3 h-100">
                        <p class="text-muted mb-1 fw-semibold">${escapeHtml(metric.label)}</p>
                        <h4 class="fw-bold text-dark mb-0">${Number(metric.value || 0).toLocaleString("id-ID")}</h4>
                    </div>
                </div>`).join("")}
        </div>`;
}

function renderRecentApiRows(rows = []) {
    if (!rows.length) {
        return `<tr><td colspan="8" class="text-center text-muted py-4">Belum ada transaksi yang perlu dipantau.</td></tr>`;
    }

    return rows.map((order) => {
        const topupStatus = order.topup_status || "-";
        const badgeTopup = topupStatus === "SUCCESS" ? "bg-success" : (topupStatus === "FAILED" ? "bg-danger" : "bg-info text-dark");
        const paymentStatus = order.payment_status || "-";
        const badgePayment = paymentStatus === "PAID" ? "bg-success" : (paymentStatus === "UNPAID" ? "bg-warning text-dark" : "bg-secondary");
        return `
            <tr>
                <td><code>${escapeHtml(String(order.id || "").slice(0, 12))}</code></td>
                <td>${escapeHtml(order.phone || "-")}</td>
                <td>${escapeHtml(order.nominal || "-")}</td>
                <td><span class="badge ${badgePayment}">${escapeHtml(paymentStatus)}</span></td>
                <td><span class="badge ${badgeTopup}">${escapeHtml(topupStatus)}</span></td>
                <td>${Number(order.provider_retry_count || 0).toLocaleString("id-ID")}</td>
                <td class="small text-danger">${escapeHtml(order.provider_last_error || "-")}</td>
                <td><small class="text-muted">${formatDate(order.created_at)}</small></td>
            </tr>`;
    }).join("");
}

function renderProviderLive(data) {
    const live = data.provider_live || {};
    const balance = live.digiflazz_balance || {};
    const channels = live.tripay_channels || {};
    return `
        <div class="row g-3 mb-4">
            <div class="col-md-6">
                <div class="api-status-card h-100">
                    <div class="api-status-title">
                        <div>
                            <div class="fw-bold"><i class="bi bi-wallet2 me-1"></i> Saldo Digiflazz</div>
                            <div class="text-muted small">${escapeHtml(balance.message || "Cek saldo deposit buyer")}</div>
                        </div>
                        <span class="badge ${balance.low_balance ? "bg-danger" : "bg-success"}">${balance.low_balance ? "Rendah" : "OK"}</span>
                    </div>
                    <div class="config-row"><span>Deposit</span><strong>${formatCurrency(balance.deposit || 0)}</strong></div>
                    <div class="config-row"><span>Threshold</span><strong>${formatCurrency(balance.threshold || 0)}</strong></div>
                    <div class="d-flex flex-wrap gap-2 mt-3">
                        <button class="btn btn-sm btn-outline-primary" onclick="refreshDigiflazzBalance()">Cek Saldo</button>
                        <button class="btn btn-sm btn-warning" onclick="createDigiflazzDeposit()">Tiket Deposit</button>
                    </div>
                </div>
            </div>
            <div class="col-md-6">
                <div class="api-status-card h-100">
                    <div class="api-status-title">
                        <div>
                            <div class="fw-bold"><i class="bi bi-credit-card me-1"></i> Channel Tripay</div>
                            <div class="text-muted small">${escapeHtml(channels.message || "Channel aktif merchant")}</div>
                        </div>
                        <span class="badge bg-info text-dark">${Number(channels.count || 0).toLocaleString("id-ID")} channel</span>
                    </div>
                    <div class="d-flex flex-wrap gap-2 mt-3">
                        <button class="btn btn-sm btn-outline-primary" onclick="showTripayChannels()">Lihat Channel</button>
                        <button class="btn btn-sm btn-outline-secondary" onclick="showTripayTransactions()">Rekonsiliasi</button>
                    </div>
                </div>
            </div>
        </div>`;
}

function renderOperationalChecklist(data) {
    const metrics = data.metrics || [];
    const live = data.provider_live || {};
    const balance = live.digiflazz_balance || {};
    const metricValue = (label) => Number((metrics.find((item) => item.label === label) || {}).value || 0);
    const waitingPayment = metricValue("Belum Bayar");
    const engineQueue = metricValue("Queue Engine");
    const pendingProvider = metricValue("Menunggu Provider");
    const retryingProvider = metricValue("Retry Provider");
    const balanceLow = Boolean(balance.low_balance);

    const rows = [
        {
            icon: "bi-wallet2",
            title: "Saldo Digiflazz",
            status: balanceLow ? "Perlu deposit" : "Aman",
            badge: balanceLow ? "bg-danger" : "bg-success",
            note: balanceLow ? "Buat tiket deposit sebelum order ramai." : "Saldo masih di atas threshold.",
            action: `<button class="btn btn-sm btn-warning" onclick="createDigiflazzDeposit()">Tiket Deposit</button>`
        },
        {
            icon: "bi-credit-card",
            title: "Tripay",
            status: `${waitingPayment.toLocaleString("id-ID")} belum bayar`,
            badge: waitingPayment ? "bg-warning text-dark" : "bg-success",
            note: "Engine akan sync otomatis; gunakan rekonsiliasi jika callback telat.",
            action: `<button class="btn btn-sm btn-outline-secondary" onclick="showTripayTransactions()">Cek Transaksi</button>`
        },
        {
            icon: "bi-lightning-charge",
            title: "Provider",
            status: `${pendingProvider.toLocaleString("id-ID")} pending`,
            badge: pendingProvider || retryingProvider ? "bg-warning text-dark" : "bg-success",
            note: `${engineQueue.toLocaleString("id-ID")} order queue, ${retryingProvider.toLocaleString("id-ID")} retry provider.`,
            action: `<button class="btn btn-sm btn-outline-primary" onclick="loadOrders()">Lihat Order</button>`
        },
        {
            icon: "bi-box-seam",
            title: "Produk",
            status: "Sinkron rutin",
            badge: "bg-info text-dark",
            note: "Tarik prepaid dan postpaid dari Digiflazz, lalu cek markup.",
            action: `<button class="btn btn-sm btn-outline-primary" onclick="syncProviderProducts()">Sync Produk</button>`
        },
        {
            icon: "bi-inbox",
            title: "Notifikasi",
            status: "Outbox",
            badge: "bg-secondary",
            note: "Cek pesan sistem, webhook, dan riwayat audit operasional.",
            action: `<button class="btn btn-sm btn-outline-secondary" onclick="loadAuditLogs()">Buka Audit</button>`
        }
    ];

    return `
        <div class="api-status-card mb-4">
            <div class="api-status-title mb-3">
                <div>
                    <div class="fw-bold"><i class="bi bi-list-check me-1"></i> SOP Operasional Harian</div>
                    <div class="text-muted small">Urutan kerja admin untuk menjaga transaksi tetap lancar.</div>
                </div>
            </div>
            <div class="row g-3">
                ${rows.map((item) => `
                    <div class="col-md-6 col-xl">
                        <div class="border rounded-3 p-3 h-100">
                            <div class="d-flex justify-content-between gap-2 mb-2">
                                <strong><i class="bi ${item.icon} me-1"></i> ${escapeHtml(item.title)}</strong>
                                <span class="badge ${item.badge}">${escapeHtml(item.status)}</span>
                            </div>
                            <p class="text-muted small mb-3">${escapeHtml(item.note)}</p>
                            ${item.action}
                        </div>
                    </div>`).join("")}
            </div>
        </div>`;
}

function renderApiMonitor(data) {
    const providers = data.providers || {};
    const engine = data.engine || {};
    const metrics = data.metrics || [];

    byId("api_monitor_content").innerHTML = `
        <div class="d-flex flex-wrap justify-content-between align-items-start gap-3 mb-4">
            <div>
                <h5 class="fw-bold mb-1">Status API & Engine Transaksi</h5>
            </div>
            <button class="btn btn-sm btn-outline-primary" onclick="loadApiMonitor()"><i class="bi bi-arrow-clockwise"></i> Refresh</button>
        </div>

        ${renderMetricCards(metrics)}
        ${renderProviderLive(data)}
        ${renderOperationalChecklist(data)}
        ${renderApiSettings(data)}

        <div class="api-status-grid mb-4">
            ${renderProviderCard(providers.tripay || { name: "Tripay" }, "bi-credit-card")}
            ${renderProviderCard(providers.digiflazz || { name: "Digiflazz" }, "bi-lightning-charge")}
            <div class="api-status-card">
                <div class="api-status-title">
                    <div>
                        <div class="fw-bold"><i class="bi bi-cpu me-1"></i> Engine Polling</div>
                        <div class="text-muted small">app.engine.auto_engine_loop</div>
                    </div>
                    <span class="status-dot ok"></span>
                </div>
                <div class="config-row"><span>Interval cek</span><strong>${Number(engine.poll_interval_seconds || 0)} detik</strong></div>
                <div class="config-row"><span>Maks retry provider</span><strong>${Number(engine.max_provider_retry || 0)} kali</strong></div>
                <div class="config-row"><span>Status worker</span><strong>${escapeHtml(engine.status_label || "Aktif saat aplikasi berjalan")}</strong></div>
            </div>
        </div>

        <div class="mb-4">
            <h6 class="fw-bold mb-3">Alur Proses</h6>
            ${renderFlowSteps(data.flow || [])}
        </div>

        <div class="table-custom p-3">
            <div class="d-flex justify-content-between align-items-center mb-3">
                <h6 class="fw-bold mb-0">Transaksi Terbaru yang Dipantau</h6>
            </div>
            <div class="table-responsive">
                <table class="table table-hover align-middle mb-0">
                    <thead class="table-light">
                        <tr>
                            <th>Order</th>
                            <th>No. HP</th>
                            <th>SKU</th>
                            <th>Tripay</th>
                            <th>Digiflazz</th>
                            <th>Retry</th>
                            <th>Error</th>
                            <th>Waktu</th>
                        </tr>
                    </thead>
                    <tbody>${renderRecentApiRows(data.recent_orders || [])}</tbody>
                </table>
            </div>
        </div>`;
}

function inputValue(id) {
    return (byId(id)?.value || "").trim();
}

function collectProviderSettingsPayload() {
    const payload = {
        active_payment_provider: inputValue("api_active_payment_provider"),
        active_topup_provider: inputValue("api_active_topup_provider"),
        backup_topup_provider: inputValue("api_backup_topup_provider"),
        topup_failover_enabled: byId("api_topup_failover_enabled")?.checked ? 1 : 0,
        tripay_invoice_expiry_minutes: Number(inputValue("api_tripay_invoice_expiry_minutes") || 30),
        digiflazz_testing_enabled: byId("api_digiflazz_testing_enabled")?.checked ? 1 : 0,
        digiflazz_max_price_margin_percent: Number(inputValue("api_digiflazz_max_price_margin_percent") || 10),
        digiflazz_low_balance_threshold: Number(inputValue("api_digiflazz_low_balance_threshold") || 50000),
        tripay_base_url: inputValue("api_tripay_base_url"),
        digiflazz_base_url: inputValue("api_digiflazz_base_url"),
        digiflazz_username: inputValue("api_digiflazz_username")
    };

    return payload;
}

window.saveProviderSettings = async function(event) {
    if (event) event.preventDefault();
    if (!ensurePermission("api:monitor")) return;

    const submitButton = event?.target?.querySelector("button[type='submit']");
    const originalText = submitButton ? submitButton.innerHTML : "";
    if (submitButton) {
        submitButton.disabled = true;
        submitButton.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Menyimpan...`;
    }

    try {
        await api("/admin/api/provider-settings", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(collectProviderSettingsPayload())
        });
        await window.loadApiMonitor();
        alert("Pengaturan API transaksi berhasil disimpan.");
    } catch (error) {
        showError(error, "Gagal menyimpan pengaturan API transaksi.");
    } finally {
        if (submitButton) {
            submitButton.disabled = false;
            submitButton.innerHTML = originalText;
        }
    }
};

window.refreshDigiflazzBalance = async function() {
    if (!ensurePermission("api:monitor")) return;
    try {
        const data = await api("/admin/api/provider/digiflazz/balance");
        const deposit = data.response?.data?.deposit || 0;
        alert(`Saldo Digiflazz: ${formatCurrency(deposit)}`);
        await window.loadApiMonitor();
    } catch (error) {
        showError(error, "Gagal cek saldo Digiflazz.");
    }
};

window.syncProviderProducts = async function() {
    if (!ensurePermission("products:manage")) return;
    if (!confirm("Tarik data produk terbaru dari Digiflazz?")) return;
    try {
        const res = await api("/admin/sync-products", { method: "POST" });
        alert(res.message || "Sinkronisasi produk selesai.");
        await window.loadApiMonitor();
    } catch (error) {
        showError(error, "Gagal sinkronisasi produk.");
    }
};

window.createDigiflazzDeposit = async function() {
    if (!ensurePermission("api:monitor")) return;
    const amount = Number(prompt("Nominal deposit Digiflazz:", "100000") || 0);
    if (!amount) return;
    const bank = prompt("Bank tujuan deposit:", "BCA");
    if (!bank) return;
    const ownerName = prompt("Nama pemilik rekening pengirim:", currentAdmin?.username || "LIXAFA");
    if (!ownerName) return;
    try {
        const data = await api("/admin/api/provider/digiflazz/deposit", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ amount, bank, owner_name: ownerName })
        });
        alert(JSON.stringify(data.response?.data || data.response, null, 2));
    } catch (error) {
        showError(error, "Gagal membuat tiket deposit Digiflazz.");
    }
};

window.showTripayChannels = async function() {
    if (!ensurePermission("api:monitor")) return;
    try {
        const data = await api("/admin/api/provider/tripay/channels");
        const rows = Array.isArray(data.response?.data) ? data.response.data : [];
        alert(rows.length ? rows.map((item) => `${item.code || item.method || "-"} - ${item.name || item.payment_name || "-"}`).join("\n") : JSON.stringify(data.response, null, 2));
    } catch (error) {
        showError(error, "Gagal mengambil channel Tripay.");
    }
};

window.showTripayTransactions = async function() {
    if (!ensurePermission("api:monitor")) return;
    const merchantRef = prompt("Filter merchant_ref (kosongkan untuk transaksi terbaru):", "");
    try {
        const params = new URLSearchParams();
        if (merchantRef) params.set("merchant_ref", merchantRef);
        const data = await api(`/admin/api/provider/tripay/transactions?${params.toString()}`);
        alert(JSON.stringify(data.response?.data || data.response, null, 2));
    } catch (error) {
        showError(error, "Gagal mengambil transaksi Tripay.");
    }
};

window.loadApiMonitor = async function() {
    if (!ensurePermission("api:monitor")) return;
    hideWorkspace();
    setActiveMenu("nav-api-monitor");
    byId("page-title").innerText = "API Transaksi";
    byId("api_monitor_container").style.display = "block";
    byId("api_monitor_content").innerHTML = `<div class="text-center p-5"><div class="spinner-border text-warning"></div><br>Memuat status API...</div>`;

    try {
        const data = await api("/admin/api/provider-status");
        renderApiMonitor(data);
    } catch (error) {
        byId("api_monitor_content").innerHTML = `<div class="alert alert-danger mb-0">Gagal memuat status API transaksi.</div>`;
        showError(error, "Gagal memuat status API transaksi.");
    }
};

window.loadSettings = async function() {
    if (!ensurePermission("settings:manage")) return;
    hideWorkspace();
    setActiveMenu("nav-settings");
    byId("page-title").innerText = "Identitas Website";
    byId("settings_container").style.display = "block";

    try {
        const data = await api("/admin/api/site-settings");
        byId("site_name_input").value = data.site_name || "";
        byId("site_logo_url_input").value = data.logo_url || "";
        byId("site_footer_text_input").value = data.footer_text || "";
    } catch (error) {
        showError(error, "Gagal memuat pengaturan website.");
    }
};

window.saveSiteSettings = async function() {
    if (!ensurePermission("settings:manage")) return;
    let logoUrl = byId("site_logo_url_input").value.trim();
    const fileInput = byId("site_logo_file_input");

    try {
        if (fileInput.files && fileInput.files[0]) {
            const formData = new FormData();
            formData.append("file", fileInput.files[0]);
            formData.append("folder", "logos");
            const uploaded = await api("/admin/upload-image", {
                method: "POST",
                body: formData
            });
            logoUrl = uploaded.url || logoUrl;
            byId("site_logo_url_input").value = logoUrl;
        }

        await api("/admin/api/site-settings", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                site_name: byId("site_name_input").value,
                logo_url: logoUrl,
                footer_text: byId("site_footer_text_input").value
            })
        });

        fileInput.value = "";
        await applyBranding();
        alert("Pengaturan website berhasil disimpan.");
    } catch (error) {
        showError(error, "Gagal menyimpan pengaturan website.");
    }
};

function bindPromoListControls() {
    const refreshFromControls = () => {
        promoViewState.search = byId("promo_search")?.value.trim() || "";
        promoViewState.status = byId("promo_filter_status")?.value || "";
        promoViewState.type = byId("promo_filter_type")?.value || "";
        promoViewState.scope = byId("promo_filter_scope")?.value || "";
        promoViewState.sort = byId("promo_sort")?.value || "updated_desc";
        promoViewState.pageSize = Number(byId("promo_page_size")?.value || 10);
        promoViewState.page = 1;
        renderPromoTable();
    };
    byId("promo_search")?.addEventListener("input", refreshFromControls);
    ["promo_filter_status", "promo_filter_type", "promo_filter_scope", "promo_sort", "promo_page_size"].forEach((id) => byId(id)?.addEventListener("change", refreshFromControls));
    byId("promo_reset_filters")?.addEventListener("click", () => {
        ["promo_search", "promo_filter_status", "promo_filter_type", "promo_filter_scope"].forEach((id) => { if (byId(id)) byId(id).value = ""; });
        if (byId("promo_sort")) byId("promo_sort").value = "updated_desc";
        if (byId("promo_page_size")) byId("promo_page_size").value = "10";
        refreshFromControls();
    });
    byId("promo_page_prev")?.addEventListener("click", () => { promoViewState.page -= 1; renderPromoTable(); });
    byId("promo_page_next")?.addEventListener("click", () => { promoViewState.page += 1; renderPromoTable(); });
    byId("promo_table_body")?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-promo-row-action]");
        if (!button) return;
        const promoId = Number(button.dataset.promoId || 0);
        const action = button.dataset.promoRowAction;
        if (action === "detail") window.showPromoDetail(promoId);
        else if (action === "edit") window.showEditPromoModal(promoId);
        else if (action === "duplicate") window.duplicatePromo(promoId);
        else if (action === "usage") window.showPromoUsage(promoId);
        else window.performPromoAction(promoId, action);
    });
}

window.filterTable = function() {
    const search = (byId("searchInput")?.value || "").toLowerCase();
    const selectors = [
        "#main_table tbody tr",
        "#promo_table_body tr",
        "#admin_table_body tr",
        "#page_table_body tr",
        "#api_monitor_content tbody tr",
        "#report_content tbody tr",
        "#customer_content tbody tr",
        "#audit_content tbody tr"
    ];
    selectors.forEach((selector) => {
        document.querySelectorAll(selector).forEach((row) => {
            row.style.display = row.textContent.toLowerCase().includes(search) ? "" : "none";
        });
    });
};

window.logout = function() {
    localStorage.removeItem("admin_token");
    localStorage.removeItem("admin_user");
    window.location.href = "/admin";
};

document.addEventListener("DOMContentLoaded", async () => {
    addModal = getModal("addProductModal");
    editModal = getModal("editProductModal");
    promoAddModal = getModal("addPromoModal");
    promoEditModal = getModal("editPromoModal");
    promoDetailModal = getModal("promoDetailModal");
    promoUsageModal = getModal("promoUsageModal");
    adminAddModal = getModal("addAdminModal");
    adminEditModal = getModal("editAdminModal");
    pageAddModal = getModal("addPageModal");
    pageEditModal = getModal("editPageModal");

    initializePromoWizard("promo", "addPromoModal", window.saveNewPromo);
    initializePromoWizard("edit_promo", "editPromoModal", window.saveEditPromo);
    bindPromoListControls();

    bindRoleDefaults("admin_role", "admin_permissions");
    bindRoleDefaults("edit_admin_role", "edit_admin_permissions");

    ["promo", "edit_promo"].forEach((prefix) => {
        byId(`${prefix}_target_scope`)?.addEventListener("change", () => {
            populatePromoTarget(prefix);
            populatePromoAdvancedTargets(prefix, [], selectedMultiValues(`${prefix}_target_exclusions`));
            updatePromoCtaFromPreset(prefix);
            updatePromoConditionalFields(prefix);
        });
        byId(`${prefix}_target_value`)?.addEventListener("change", () => { updatePromoCtaFromPreset(prefix); renderPromoTargetImpact(prefix); });
        byId(`${prefix}_target_values`)?.addEventListener("change", () => renderPromoTargetImpact(prefix));
        byId(`${prefix}_target_exclusions`)?.addEventListener("change", () => renderPromoTargetImpact(prefix));
        byId(`${prefix}_usage_mode`)?.addEventListener("change", () => { updatePromoUsageLimitState(prefix); updatePromoConditionalFields(prefix); });
        byId(`${prefix}_cta_preset`)?.addEventListener("change", () => updatePromoCtaFromPreset(prefix));
        byId(`${prefix}_image_file`)?.addEventListener("change", () => uploadPromoImage(prefix));
    });

    const btnSync = byId("btnSync");
    if (btnSync) {
        btnSync.addEventListener("click", async function() {
            if (!ensurePermission("products:manage")) return;
            if (!confirm("Tarik data dari Digiflazz?")) return;
            const originalText = this.innerHTML;
            this.disabled = true;
            this.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Loading...`;
            try {
                const res = await api("/admin/sync-products", { method: "POST" });
                alert(res.message || "Sinkronisasi selesai.");
                window.loadProducts();
            } catch (error) {
                showError(error, "Gagal sinkronisasi produk.");
            } finally {
                this.disabled = false;
                this.innerHTML = originalText;
            }
        });
    }

    await applyBranding();
    await loadAdminContext();
    await window.loadStats();
    loadFirstAllowedView();
});
