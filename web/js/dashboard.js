const token = localStorage.getItem("admin_token");
if (!token) {
    window.location.href = "/admin";
}

let addModal;
let editModal;
let promoAddModal;
let promoEditModal;
let productState = { categories: [], products: [] };
let promoState = [];

async function api(url, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("token", token);

    if (!(options.body instanceof FormData) && !headers.has("Content-Type") && options.method && options.method !== "GET") {
        headers.set("Content-Type", "application/json");
    }

    const res = await fetch(url, { ...options, headers });
    const text = await res.text();
    let data = {};
    try {
        data = text ? JSON.parse(text) : {};
    } catch (err) {
        data = { message: text };
    }

    if (!res.ok) {
        const error = new Error(data.detail || data.message || "Request gagal");
        error.response = data;
        throw error;
    }

    return data;
}

function setActiveMenu(menuId) {
    document.querySelectorAll(".sidebar .nav-link").forEach((el) => el.classList.remove("active"));
    const menu = document.getElementById(menuId);
    if (menu) menu.classList.add("active");
}

function formatRupiah(value) {
    return `Rp ${Number(value || 0).toLocaleString("id-ID")}`;
}

function normalizeProductsResponse(data) {
    if (Array.isArray(data)) {
        return { categories: [], products: data };
    }
    return {
        categories: Array.isArray(data?.categories) ? data.categories : [],
        products: Array.isArray(data?.products) ? data.products : [],
    };
}

function buildProviderIndex() {
    const index = {};
    productState.products.forEach((product) => {
        index[(product.provider || "").toLowerCase()] = product;
    });
    return index;
}

function escapeAttr(value) {
    return String(value || "").replace(/'/g, "&#39;").replace(/"/g, "&quot;");
}

function decodeSkuParam(value) {
    try {
        return decodeURIComponent(value || "");
    } catch {
        return value || "";
    }
}

async function uploadFile(file, folder) {
    if (!file) return "";
    const formData = new FormData();
    formData.append("file", file);
    formData.append("folder", folder);
    const res = await api("/admin/upload-image", { method: "POST", body: formData });
    return res.url || "";
}

window.loadStats = async function () {
    try {
        const [r1, r2, r3, r4] = await Promise.all([
            api("/admin/api/revenue-today"),
            api("/admin/api/revenue-total"),
            api("/admin/api/profit-today"),
            api("/admin/api/profit-total"),
        ]);

        document.getElementById("revenue_today").innerText = formatRupiah(r1.revenue);
        document.getElementById("revenue_total").innerText = formatRupiah(r2.revenue);
        document.getElementById("profit_today").innerText = formatRupiah(r3.profit);
        document.getElementById("profit_total").innerText = formatRupiah(r4.profit);
    } catch (error) {
        console.error("Gagal load statistik:", error);
    }
};

window.loadOrders = async function () {
    setActiveMenu("nav-orders");
    document.getElementById("page-title").innerText = "Data Transaksi Terakhir";
    document.getElementById("product_actions").style.display = "none";
    document.getElementById("promo_actions").style.display = "none";
    document.getElementById("category_nav").style.display = "none";
    document.getElementById("promo_container").style.display = "none";
    document.getElementById("order_container").style.display = "block";
    document.getElementById("product_container").style.display = "none";

    document.getElementById("main_table").innerHTML = "<tr><td class='text-center'>Loading...</td></tr>";

    try {
        const data = await api("/admin/api/orders");
        let html = `
        <thead class="table-light">
            <tr><th>ID Order</th><th>No. HP</th><th>SKU</th><th>Pembayaran</th><th>Status</th><th>Waktu</th></tr>
        </thead><tbody>`;

        data.forEach((o) => {
            const badgePay = o.payment_status === "PAID" ? "bg-success" : "bg-warning text-dark";
            const badgeTop = o.topup_status === "SUCCESS" ? "bg-success" : o.topup_status === "FAILED" ? "bg-danger" : "bg-info text-dark";
            html += `
            <tr>
                <td class="text-muted"><small>${o.id.substring(0, 8)}</small></td>
                <td class="fw-bold">${o.phone}</td>
                <td><span class="badge bg-secondary">${o.nominal}</span></td>
                <td><span class="badge ${badgePay}">${o.payment_status}</span></td>
                <td><span class="badge ${badgeTop}">${o.topup_status}</span></td>
                <td><small class="text-muted">${new Date(o.created_at).toLocaleString()}</small></td>
            </tr>`;
        });
        document.getElementById("main_table").innerHTML = html + "</tbody>";
    } catch (error) {
        console.error(error);
        document.getElementById("main_table").innerHTML = `<tr><td class="text-danger">Gagal memuat transaksi</td></tr>`;
    }
};

window.showBulkMarkupModal = async function () {
    const selectElement = document.getElementById("bulk_brand");
    selectElement.innerHTML = `<option value="">Memuat...</option>`;

    const modalElement = document.getElementById("bulkMarkupModal");
    const modalInstance = bootstrap.Modal.getInstance(modalElement) || new bootstrap.Modal(modalElement);
    modalInstance.show();

    try {
        const res = await api("/admin/api/products");
        const providers = [...new Set((res.products || []).map((p) => p.provider).filter(Boolean).map((p) => p.toUpperCase()))].sort();
        let optionsHtml = `<option value="ALL">SEMUA PRODUK</option>`;
        providers.forEach((provider) => {
            optionsHtml += `<option value="${provider}">${provider}</option>`;
        });
        selectElement.innerHTML = optionsHtml;
    } catch (error) {
        console.error(error);
        selectElement.innerHTML = `<option value="">Gagal memuat provider</option>`;
    }
};

window.saveBulkMarkup = async function () {
    const brand = document.getElementById("bulk_brand").value;
    const percent = parseFloat(document.getElementById("bulk_percent").value);
    const minProfit = parseInt(document.getElementById("bulk_min_profit").value, 10) || 0;

    if (!brand) {
        alert("Pilih provider dulu.");
        return;
    }
    if (Number.isNaN(percent) || percent <= 0) {
        alert("Masukkan persen profit yang valid.");
        return;
    }

    const btn = document.querySelector("#bulkMarkupModal .btn-warning");
    const originalText = btn.innerHTML;
    btn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Memproses...`;
    btn.disabled = true;

    try {
        const res = await api("/admin/bulk-markup", {
            method: "POST",
            body: JSON.stringify({ brand, percent, min_profit: minProfit }),
        });
        alert(res.message || "Berhasil");
        bootstrap.Modal.getInstance(document.getElementById("bulkMarkupModal"))?.hide();
        await loadProducts();
    } catch (error) {
        console.error(error);
        alert(error.message || "Terjadi kesalahan.");
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
};

window.loadProducts = async function () {
    setActiveMenu("nav-products");
    document.getElementById("page-title").innerText = "Manajemen Produk";
    document.getElementById("product_actions").style.display = "block";
    document.getElementById("promo_actions").style.display = "none";
    document.getElementById("category_nav").style.display = "block";
    document.getElementById("promo_container").style.display = "none";
    document.getElementById("order_container").style.display = "none";
    document.getElementById("product_container").style.display = "block";

    document.getElementById("product_container").innerHTML = "<div class='text-center p-5'><div class='spinner-border text-primary'></div><br>Memuat Produk...</div>";

    try {
        const res = normalizeProductsResponse(await api("/admin/api/products"));
        productState = res;

        const categories = res.categories || [];
        const navContainer = document.querySelector("#category_nav ul");
        navContainer.innerHTML = "";

        if (!categories.length) {
            document.getElementById("product_container").innerHTML = "<div class='alert alert-info'>Belum ada produk di database.</div>";
            return;
        }

        categories.forEach((category, index) => {
            const isActive = index === 0 ? "active" : "";
            navContainer.innerHTML += `
                <li class="nav-item">
                    <button class="nav-link ${isActive}" onclick="switchCategory(${index}, this)">
                        ${category.name}
                    </button>
                </li>`;
        });

        renderByKategori(0);
    } catch (error) {
        console.error(error);
        document.getElementById("product_container").innerHTML = "<div class='alert alert-danger'>Gagal memuat produk.</div>";
    }
};

window.switchCategory = function (categoryIndex, element) {
    document.querySelectorAll("#category_nav .nav-link").forEach((button) => button.classList.remove("active"));
    element.classList.add("active");
    renderByKategori(categoryIndex);
};

function renderByKategori(categoryIndex) {
    const container = document.getElementById("product_container");
    const categories = productState.categories || [];
    const category = categories[categoryIndex];

    if (!category) {
        container.innerHTML = `<div class="alert alert-info">Kategori tidak ditemukan.</div>`;
        return;
    }

    if (!category.providers || !category.providers.length) {
        container.innerHTML = `<div class="alert alert-info">Belum ada produk di kategori ${category.name}.</div>`;
        return;
    }

    container.innerHTML = "";
    category.providers.forEach((provider) => {
        const rows = provider.items.map((product) => {
            const statusBadge = product.active ? `<span class="badge bg-success">Aktif</span>` : `<span class="badge bg-secondary">Mati</span>`;
            const encodedSku = encodeURIComponent(product.sku || "");
            return `
            <tr>
                <td class="small text-muted">${product.sku}</td>
                <td>
                    <div class="fw-bold">${product.name} <span class="badge text-bg-dark border border-secondary-subtle ms-1" style="font-size:10px;vertical-align:middle;">${product.provider || provider.name || "-"}</span></div>
                    <div class="small text-muted">${product.description || ""}</div>
                </td>
                <td class="small">${formatRupiah(product.cost)}</td>
                <td class="text-primary fw-bold">${formatRupiah(product.price)}</td>
                <td class="text-success small">${formatRupiah(product.profit)}</td>
                <td>${statusBadge}</td>
                <td class="text-center">
                    <button class="btn btn-sm btn-outline-primary" onclick="showEditModal('${encodedSku}')"><i class="bi bi-pencil-square"></i></button>
                    <button class="btn btn-sm ${product.active ? 'btn-outline-warning' : 'btn-outline-success'}" onclick="toggleProduct('${encodedSku}')"><i class="bi bi-power"></i></button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteProduct('${encodedSku}')"><i class="bi bi-trash"></i></button>
                </td>
            </tr>`;
        }).join("");

        container.innerHTML += `
            <div class="card border-0 shadow-sm mb-4 rounded-4 overflow-hidden">
                <div class="card-header bg-dark text-white fw-bold d-flex justify-content-between align-items-center p-3">
                    <div class="d-flex align-items-center gap-3">
                        ${provider.logo_url ? `<img src="${provider.logo_url}" alt="${provider.name}" style="width:42px;height:42px;object-fit:cover;border-radius:12px;">` : ""}
                        <div>
                            <div><i class="bi bi-tag-fill me-2 text-warning"></i>${provider.name.toUpperCase()}</div>
                            <div class="small text-white-50">${provider.description || ""}</div>
                        </div>
                    </div>
                    <span class="badge bg-secondary">${provider.items.length} Item</span>
                </div>
                <div class="table-responsive">
                    <table class="table table-hover align-middle mb-0">
                        <thead class="table-light small">
                            <tr><th>SKU</th><th>Item</th><th>Modal</th><th>Jual</th><th>Profit</th><th>Status</th><th class="text-center">Aksi</th></tr>
                        </thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
            </div>`;
    });
}

window.showAddProductModal = function () {
    ["add_provider", "add_name", "add_sku", "add_cost", "add_price", "add_category", "add_description", "add_image_url", "add_logo_url", "add_promo_title", "add_promo_text", "add_promo_badge", "add_promo_url", "add_display_order"].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = "";
    });
    const activeEl = document.getElementById("add_active");
    if (activeEl) activeEl.checked = true;
    addModal.show();
};

window.saveNewProduct = async function () {
    const imageFile = document.getElementById("add_image_file")?.files?.[0];
    const logoFile = document.getElementById("add_logo_file")?.files?.[0];
    const imageUrl = imageFile ? await uploadFile(imageFile, "products") : (document.getElementById("add_image_url").value || "").trim();
    const logoUrl = logoFile ? await uploadFile(logoFile, "logos") : (document.getElementById("add_logo_url").value || "").trim();

    const payload = {
        provider: document.getElementById("add_provider").value,
        name: document.getElementById("add_name").value,
        sku: document.getElementById("add_sku").value,
        cost: parseFloat(document.getElementById("add_cost").value),
        price: parseFloat(document.getElementById("add_price").value),
        category: document.getElementById("add_category").value,
        description: document.getElementById("add_description").value,
        image_url: imageUrl,
        logo_url: logoUrl,
        promo_title: document.getElementById("add_promo_title").value,
        promo_text: document.getElementById("add_promo_text").value,
        promo_badge: document.getElementById("add_promo_badge").value,
        promo_url: document.getElementById("add_promo_url").value,
        active: document.getElementById("add_active")?.checked ? 1 : 0,
        display_order: parseInt(document.getElementById("add_display_order")?.value || "0", 10),
    };

    await api("/admin/api/products", { method: "POST", body: JSON.stringify(payload) });
    addModal.hide();
    loadProducts();
};

window.showEditModal = function (sku) {
    const decodedSku = decodeSkuParam(sku);
    const product = productState.products.find((item) => item.sku === decodedSku);
    if (!product) return;

    document.getElementById("edit_id").value = product.sku;
    document.getElementById("edit_provider").value = product.provider || "";
    document.getElementById("edit_name").value = product.name || "";
    document.getElementById("edit_cost").value = product.cost || "";
    document.getElementById("edit_price").value = product.price || "";
    document.getElementById("edit_category").value = product.category || "";
    document.getElementById("edit_description").value = product.description || "";
    document.getElementById("edit_image_url").value = product.image_url || "";
    document.getElementById("edit_logo_url").value = product.logo_url || "";
    document.getElementById("edit_promo_title").value = product.promo_title || "";
    document.getElementById("edit_promo_text").value = product.promo_text || "";
    document.getElementById("edit_promo_badge").value = product.promo_badge || "";
    document.getElementById("edit_promo_url").value = product.promo_url || "";
    document.getElementById("edit_display_order").value = product.display_order || 0;
    document.getElementById("edit_active").checked = !!product.active;
    editModal.show();
};

window.saveEditProduct = async function () {
    const sku = (document.getElementById("edit_id").value || "").trim();
    if (!sku) {
        alert("SKU produk tidak valid.");
        return;
    }

    const updateBtn = document.querySelector("#editProductModal .btn-primary");
    const originalText = updateBtn ? updateBtn.innerHTML : "";
    if (updateBtn) {
        updateBtn.disabled = true;
        updateBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Memproses...';
    }

    try {
        const imageFile = document.getElementById("edit_image_file")?.files?.[0];
        const logoFile = document.getElementById("edit_logo_file")?.files?.[0];
        const imageUrl = imageFile ? await uploadFile(imageFile, "products") : (document.getElementById("edit_image_url").value || "").trim();
        const logoUrl = logoFile ? await uploadFile(logoFile, "logos") : (document.getElementById("edit_logo_url").value || "").trim();

        const costRaw = (document.getElementById("edit_cost").value || "").trim();
        const priceRaw = (document.getElementById("edit_price").value || "").trim();
        const cost = costRaw === "" ? null : parseFloat(costRaw);
        const price = priceRaw === "" ? null : parseFloat(priceRaw);

        if (costRaw !== "" && Number.isNaN(cost)) {
            alert("Harga modal tidak valid.");
            return;
        }
        if (priceRaw !== "" && Number.isNaN(price)) {
            alert("Harga jual tidak valid.");
            return;
        }

        const payload = {
            provider: document.getElementById("edit_provider").value,
            name: document.getElementById("edit_name").value,
            cost,
            price,
            category: document.getElementById("edit_category").value,
            description: document.getElementById("edit_description").value,
            image_url: imageUrl,
            logo_url: logoUrl,
            promo_title: document.getElementById("edit_promo_title").value,
            promo_text: document.getElementById("edit_promo_text").value,
            promo_badge: document.getElementById("edit_promo_badge").value,
            promo_url: document.getElementById("edit_promo_url").value,
            active: document.getElementById("edit_active").checked ? 1 : 0,
            display_order: parseInt(document.getElementById("edit_display_order").value || "0", 10),
        };

        await api(`/admin/api/products/${encodeURIComponent(sku)}`, { method: "PUT", body: JSON.stringify(payload) });
        editModal.hide();
        await loadProducts();
        alert("Produk berhasil diperbarui.");
    } catch (error) {
        console.error(error);
        alert(error.message || "Gagal memperbarui produk.");
    } finally {
        if (updateBtn) {
            updateBtn.disabled = false;
            updateBtn.innerHTML = originalText;
        }
    }
};

window.toggleProduct = async function (sku) {
    const decodedSku = decodeSkuParam(sku);
    await api(`/admin/api/products/${encodeURIComponent(decodedSku)}/toggle`, { method: "PUT" });
    loadProducts();
};

window.deleteProduct = async function (sku) {
    const decodedSku = decodeSkuParam(sku);
    if (confirm(`Hapus permanen SKU: ${decodedSku}?`)) {
        await api(`/admin/api/products/${encodeURIComponent(decodedSku)}`, { method: "DELETE" });
        loadProducts();
    }
};

window.loadPromos = async function () {
    setActiveMenu("nav-promos");
    document.getElementById("page-title").innerText = "Manajemen Promo";
    document.getElementById("product_actions").style.display = "none";
    document.getElementById("promo_actions").style.display = "block";
    document.getElementById("category_nav").style.display = "none";
    document.getElementById("order_container").style.display = "none";
    document.getElementById("product_container").style.display = "none";
    document.getElementById("promo_container").style.display = "block";

    const body = document.getElementById("promo_table_body");
    body.innerHTML = `<tr><td colspan="7" class="text-center">Memuat promo...</td></tr>`;

    try {
        promoState = await api("/admin/api/promos");
        if (!promoState.length) {
            body.innerHTML = `<tr><td colspan="7" class="text-center text-muted">Belum ada promo</td></tr>`;
            return;
        }

        body.innerHTML = promoState.map((promo) => `
            <tr>
                <td><strong>${promo.title}</strong><div class="small text-muted">${promo.code || "-"}</div></td>
                <td>${promo.badge || "-"}</td>
                <td>${promo.active ? "<span class='badge bg-success'>Aktif</span>" : "<span class='badge bg-secondary'>Nonaktif</span>"}</td>
                <td>${promo.show_on_website ? "<span class='badge bg-info text-dark'>Tampil</span>" : "<span class='badge bg-dark'>Disembunyikan</span>"}</td>
                <td class="small">${promo.starts_at || "-"}<br>${promo.ends_at || "-"}</td>
                <td class="small">${promo.description || ""}</td>
                <td class="text-end">
                    <button class="btn btn-sm btn-outline-primary" onclick="showEditPromoModal(${promo.id})">Edit</button>
                    <button class="btn btn-sm btn-outline-info" onclick="duplicatePromo(${promo.id})">Duplikat</button>
                    <button class="btn btn-sm ${promo.show_on_website ? "btn-outline-secondary" : "btn-outline-info"}" onclick="togglePromoWebsite(${promo.id}, ${promo.show_on_website ? 0 : 1})">${promo.show_on_website ? "Sembunyikan Web" : "Tampilkan Web"}</button>
                    <button class="btn btn-sm ${promo.active ? "btn-outline-warning" : "btn-outline-success"}" onclick="togglePromoActive(${promo.id}, ${promo.active ? 0 : 1})">${promo.active ? "Nonaktifkan" : "Aktifkan"}</button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deletePromo(${promo.id})">Hapus</button>
                </td>
            </tr>
        `).join("");
    } catch (error) {
        console.error(error);
        body.innerHTML = `<tr><td colspan="7" class="text-danger text-center">Gagal memuat promo</td></tr>`;
    }
};

window.showAddPromoModal = function () {
    [
        "promo_title", "promo_code", "promo_description", "promo_badge",
        "promo_cta_text", "promo_cta_url", "promo_image_url", "promo_starts_at", "promo_ends_at",
        "promo_target_value", "promo_discount_value", "promo_max_discount"
    ].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = "";
    });
    document.getElementById("promo_rule_type").value = "content";
    document.getElementById("promo_target_scope").value = "all";
    document.getElementById("promo_discount_type").value = "";
    const webVisible = document.getElementById("promo_show_on_website");
    if (webVisible) webVisible.checked = true;
    const active = document.getElementById("promo_active");
    if (active) active.checked = true;
    promoAddModal.show();
};

window.saveNewPromo = async function () {
    const imageFile = document.getElementById("promo_image_file")?.files?.[0];
    const imageUrl = imageFile ? await uploadFile(imageFile, "promos") : (document.getElementById("promo_image_url").value || "").trim();

    const payload = {
        title: document.getElementById("promo_title").value,
        code: document.getElementById("promo_code").value,
        description: document.getElementById("promo_description").value,
        badge: document.getElementById("promo_badge").value,
        rule_type: document.getElementById("promo_rule_type").value,
        target_scope: document.getElementById("promo_target_scope").value,
        target_value: document.getElementById("promo_target_value").value,
        discount_type: document.getElementById("promo_discount_type").value,
        discount_value: document.getElementById("promo_discount_value").value ? parseFloat(document.getElementById("promo_discount_value").value) : null,
        max_discount: document.getElementById("promo_max_discount").value ? parseFloat(document.getElementById("promo_max_discount").value) : null,
        cta_text: document.getElementById("promo_cta_text").value,
        cta_url: document.getElementById("promo_cta_url").value,
        image_url: imageUrl,
        starts_at: document.getElementById("promo_starts_at").value,
        ends_at: document.getElementById("promo_ends_at").value,
        show_on_website: document.getElementById("promo_show_on_website")?.checked ? 1 : 0,
        active: document.getElementById("promo_active")?.checked ? 1 : 0,
    };

    await api("/admin/api/promos", { method: "POST", body: JSON.stringify(payload) });
    promoAddModal.hide();
    loadPromos();
};

window.showEditPromoModal = function (promoId) {
    const promo = promoState.find((item) => item.id === promoId);
    if (!promo) return;

    document.getElementById("edit_promo_id").value = promo.id;
    document.getElementById("edit_promo_title").value = promo.title || "";
    document.getElementById("edit_promo_code").value = promo.code || "";
    document.getElementById("edit_promo_description").value = promo.description || "";
    document.getElementById("edit_promo_badge").value = promo.badge || "";
    document.getElementById("edit_promo_rule_type").value = promo.rule_type || "content";
    document.getElementById("edit_promo_target_scope").value = promo.target_scope || "all";
    document.getElementById("edit_promo_target_value").value = promo.target_value || "";
    document.getElementById("edit_promo_discount_type").value = promo.discount_type || "";
    document.getElementById("edit_promo_discount_value").value = promo.discount_value || "";
    document.getElementById("edit_promo_max_discount").value = promo.max_discount || "";
    document.getElementById("edit_promo_cta_text").value = promo.cta_text || "";
    document.getElementById("edit_promo_cta_url").value = promo.cta_url || "";
    document.getElementById("edit_promo_image_url").value = promo.image_url || "";
    document.getElementById("edit_promo_starts_at").value = promo.starts_at || "";
    document.getElementById("edit_promo_ends_at").value = promo.ends_at || "";
    document.getElementById("edit_promo_show_on_website").checked = !!promo.show_on_website;
    document.getElementById("edit_promo_active").checked = !!promo.active;
    promoEditModal.show();
};

window.saveEditPromo = async function () {
    const promoId = document.getElementById("edit_promo_id").value;
    const imageFile = document.getElementById("edit_promo_image_file")?.files?.[0];
    const imageUrl = imageFile ? await uploadFile(imageFile, "promos") : (document.getElementById("edit_promo_image_url").value || "").trim();

    const payload = {
        title: document.getElementById("edit_promo_title").value,
        code: document.getElementById("edit_promo_code").value,
        description: document.getElementById("edit_promo_description").value,
        badge: document.getElementById("edit_promo_badge").value,
        rule_type: document.getElementById("edit_promo_rule_type").value,
        target_scope: document.getElementById("edit_promo_target_scope").value,
        target_value: document.getElementById("edit_promo_target_value").value,
        discount_type: document.getElementById("edit_promo_discount_type").value,
        discount_value: document.getElementById("edit_promo_discount_value").value ? parseFloat(document.getElementById("edit_promo_discount_value").value) : null,
        max_discount: document.getElementById("edit_promo_max_discount").value ? parseFloat(document.getElementById("edit_promo_max_discount").value) : null,
        cta_text: document.getElementById("edit_promo_cta_text").value,
        cta_url: document.getElementById("edit_promo_cta_url").value,
        image_url: imageUrl,
        starts_at: document.getElementById("edit_promo_starts_at").value,
        ends_at: document.getElementById("edit_promo_ends_at").value,
        show_on_website: document.getElementById("edit_promo_show_on_website").checked ? 1 : 0,
        active: document.getElementById("edit_promo_active").checked ? 1 : 0,
    };

    await api(`/admin/api/promos/${promoId}`, { method: "PUT", body: JSON.stringify(payload) });
    promoEditModal.hide();
    loadPromos();
};

window.deletePromo = async function (promoId) {
    if (confirm("Hapus promo ini?")) {
        await api(`/admin/api/promos/${promoId}`, { method: "DELETE" });
        loadPromos();
    }
};

window.togglePromoActive = async function (promoId, nextActive) {
    await api(`/admin/api/promos/${promoId}`, {
        method: "PUT",
        body: JSON.stringify({ active: Number(nextActive) }),
    });
    loadPromos();
};

window.togglePromoWebsite = async function (promoId, nextValue) {
    await api(`/admin/api/promos/${promoId}`, {
        method: "PUT",
        body: JSON.stringify({ show_on_website: Number(nextValue) }),
    });
    loadPromos();
};

window.duplicatePromo = async function (promoId) {
    const source = promoState.find((item) => item.id === promoId);
    if (!source) return;

    const payload = {
        title: `${source.title || "Promo"} Copy`,
        code: source.code || "",
        description: source.description || "",
        badge: source.badge || "",
        rule_type: source.rule_type || "content",
        target_scope: source.target_scope || "all",
        target_value: source.target_value || "",
        discount_type: source.discount_type || "",
        discount_value: source.discount_value ?? null,
        max_discount: source.max_discount ?? null,
        cta_text: source.cta_text || "",
        cta_url: source.cta_url || "",
        image_url: source.image_url || "",
        starts_at: source.starts_at || "",
        ends_at: source.ends_at || "",
        show_on_website: source.show_on_website ? 1 : 0,
        active: source.active ? 1 : 0,
    };

    await api("/admin/api/promos", { method: "POST", body: JSON.stringify(payload) });
    loadPromos();
};

window.filterTable = function () {
    const query = document.getElementById("searchInput")?.value?.toLowerCase().trim() || "";
    const rows = document.querySelectorAll("#main_table tbody tr, #promo_table_body tr");

    rows.forEach((row) => {
        const text = row.innerText.toLowerCase();
        row.style.display = text.includes(query) ? "" : "none";
    });
};

window.logout = function () {
    localStorage.removeItem("admin_token");
    window.location.href = "/admin";
};

document.addEventListener("DOMContentLoaded", () => {
    addModal = new bootstrap.Modal(document.getElementById("addProductModal"));
    editModal = new bootstrap.Modal(document.getElementById("editProductModal"));
    promoAddModal = new bootstrap.Modal(document.getElementById("addPromoModal"));
    promoEditModal = new bootstrap.Modal(document.getElementById("editPromoModal"));

    const btnSync = document.getElementById("btnSync");
    if (btnSync) {
        btnSync.addEventListener("click", async function () {
            if (!confirm("Tarik data dari Digiflazz?")) return;
            const originalText = this.innerHTML;
            this.disabled = true;
            this.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Loading...';
            try {
                const res = await api("/admin/sync-products", { method: "POST" });
                alert(res.message);
                loadProducts();
            } catch (error) {
                alert(error.message || "Gagal koneksi.");
            } finally {
                this.disabled = false;
                this.innerHTML = originalText;
            }
        });
    }

    loadStats();
    loadOrders();
});