document.addEventListener("DOMContentLoaded", () => {
    const sidebar = document.getElementById("sidebar");
    const overlay = document.getElementById("sidebarOverlay");
    const mobileMenuButton = document.getElementById(
        "mobileMenuButton"
    );

    function openSidebar() {
        if (!sidebar || !overlay) {
            return;
        }

        sidebar.classList.add("open");
        overlay.classList.add("visible");
    }

    function closeSidebar() {
        if (!sidebar || !overlay) {
            return;
        }

        sidebar.classList.remove("open");
        overlay.classList.remove("visible");
    }

    mobileMenuButton?.addEventListener(
        "click",
        openSidebar
    );

    overlay?.addEventListener(
        "click",
        closeSidebar
    );

    document
        .querySelectorAll(".alert-close")
        .forEach((button) => {
            button.addEventListener("click", () => {
                button.closest(".alert")?.remove();
            });
        });

    document
        .querySelectorAll(".confirm-form")
        .forEach((form) => {
            form.addEventListener("submit", (event) => {
                const message =
                    form.dataset.confirm ||
                    "Are you sure?";

                if (!window.confirm(message)) {
                    event.preventDefault();
                }
            });
        });

    document
        .querySelectorAll("[data-password-toggle]")
        .forEach((button) => {
            button.addEventListener("click", () => {
                const container =
                    button.closest(".password-field");

                const input =
                    container?.querySelector("input");

                if (!input) {
                    return;
                }

                const showing =
                    input.type === "text";

                input.type = showing
                    ? "password"
                    : "text";

                button.textContent = showing
                    ? "Show"
                    : "Hide";
            });
        });

    initializeFiscalPeriodModal();
    initializeDynamicFormsets();
    initializeSettingsMenu();
    initializePartnersMenu();
    initializeDeletionReview();
});


function initializeDeletionReview() {
    document.querySelectorAll("[data-product-decision]").forEach((row) => {
        const action = row.querySelector("[data-deletion-action]");
        const replacement = row.querySelector("[data-reassignment]");
        const select = replacement.querySelector("select");
        function updateReplacement() {
            const reassign = action.value === "reassign";
            replacement.hidden = !reassign;
            select.disabled = !reassign;
            select.required = reassign;
        }
        action.addEventListener("change", updateReplacement);
        updateReplacement();
    });
}


function initializePartnersMenu() {
    const menu = document.querySelector("[data-partners-menu]");
    if (!menu) {
        return;
    }

    const toggle = menu.querySelector("[data-partners-toggle]");
    const links = menu.querySelector(".partners-links");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

    function setOpen(open) {
        toggle.setAttribute("aria-expanded", String(open));
        links.inert = !open;
        menu.classList.toggle("is-open", open);
    }

    toggle.addEventListener("click", () => {
        setOpen(toggle.getAttribute("aria-expanded") !== "true");
    });

    menu.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            setOpen(false);
            toggle.focus();
        }
    });

    links.querySelectorAll("a").forEach((link) => {
        link.addEventListener("click", (event) => {
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) {
                return;
            }
            event.preventDefault();
            setOpen(false);
            toggle.focus();
            window.setTimeout(() => {
                window.location.assign(link.href);
            }, reducedMotion.matches ? 0 : 200);
        });
    });
}


function initializeSettingsMenu() {
    const menu = document.querySelector("[data-settings-menu]");

    if (!menu) {
        return;
    }

    const toggle = menu.querySelector("[data-settings-toggle]");
    const dropdown = menu.querySelector("[data-settings-dropdown]");

    function setOpen(open) {
        dropdown.hidden = !open;
        toggle.setAttribute("aria-expanded", String(open));
    }

    toggle.addEventListener("click", () => {
        setOpen(dropdown.hidden);
    });

    document.addEventListener("click", (event) => {
        if (!menu.contains(event.target)) {
            setOpen(false);
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !dropdown.hidden) {
            setOpen(false);
            toggle.focus();
        }
    });
}


function initializeDynamicFormsets() {
    document
        .querySelectorAll("[data-formset]")
        .forEach((container) => {
            const prefix = container.dataset.formsetPrefix;
            const rows = container.querySelector(
                "[data-formset-rows]"
            );
            const template = container.querySelector(
                "[data-formset-empty-form]"
            );
            const addButton = container.querySelector(
                "[data-formset-add]"
            );
            const totalForms = document.getElementById(
                `id_${prefix}-TOTAL_FORMS`
            );

            if (!rows || !template || !addButton || !totalForms) {
                return;
            }

            addButton.addEventListener("click", () => {
                const index = parseInt(totalForms.value, 10);

                const html = template.innerHTML.replace(
                    /__prefix__/g,
                    index
                );

                rows.insertAdjacentHTML("beforeend", html);

                totalForms.value = index + 1;
            });
        });
}


function getCookie(name) {
    const cookies = document.cookie
        ? document.cookie.split(";")
        : [];

    for (const cookieValue of cookies) {
        const cookie = cookieValue.trim();

        if (
            cookie.startsWith(`${name}=`)
        ) {
            return decodeURIComponent(
                cookie.substring(name.length + 1)
            );
        }
    }

    return "";
}


function getErrorMessage(data) {
    if (!data) {
        return "The action could not be completed.";
    }

    if (typeof data === "string") {
        return data;
    }

    if (data.detail) {
        return data.detail;
    }

    if (Array.isArray(data)) {
        return data.join(" ");
    }

    const firstValue = Object.values(data)[0];

    if (Array.isArray(firstValue)) {
        return firstValue.join(" ");
    }

    return (
        firstValue ||
        "The action could not be completed."
    );
}


function initializeFiscalPeriodModal() {
    const modal = document.getElementById(
        "closePeriodModal"
    );

    const form = document.getElementById(
        "closePeriodForm"
    );

    if (!modal || !form) {
        return;
    }

    const label = document.getElementById(
        "closePeriodLabel"
    );

    const reasonInput = document.getElementById(
        "closePeriodReason"
    );

    const errorElement = document.getElementById(
        "closePeriodError"
    );

    const confirmButton = document.getElementById(
        "confirmClosePeriod"
    );

    const cancelButton = document.getElementById(
        "cancelClosePeriod"
    );

    const closeButton = document.getElementById(
        "closePeriodModalButton"
    );

    let closeUrl = "";

    document
        .querySelectorAll(".close-period-button")
        .forEach((button) => {
            button.addEventListener("click", () => {
                closeUrl = button.dataset.url || "";

                label.textContent =
                    button.dataset.period || "";

                reasonInput.value = "";
                errorElement.textContent = "";

                modal.showModal();
                reasonInput.focus();
            });
        });

    function closeModal() {
        modal.close();
        closeUrl = "";
        errorElement.textContent = "";
    }

    cancelButton?.addEventListener(
        "click",
        closeModal
    );

    closeButton?.addEventListener(
        "click",
        closeModal
    );

    form.addEventListener(
        "submit",
        async (event) => {
            event.preventDefault();

            const reason =
                reasonInput.value.trim();

            if (!reason) {
                errorElement.textContent =
                    "A closing reason is required.";

                reasonInput.focus();

                return;
            }

            if (!closeUrl) {
                errorElement.textContent =
                    "The fiscal period URL is missing.";

                return;
            }

            confirmButton.disabled = true;
            confirmButton.textContent = "Closing...";
            errorElement.textContent = "";

            try {
                const response = await fetch(
                    closeUrl,
                    {
                        method: "POST",
                        credentials: "same-origin",
                        headers: {
                            "Content-Type":
                                "application/json",
                            "X-CSRFToken":
                                getCookie("csrftoken"),
                            "X-Requested-With":
                                "XMLHttpRequest",
                        },
                        body: JSON.stringify({
                            reason,
                        }),
                    }
                );

                const data = await response
                    .json()
                    .catch(() => null);

                if (!response.ok) {
                    throw new Error(
                        getErrorMessage(data)
                    );
                }

                window.location.reload();
            } catch (error) {
                errorElement.textContent =
                    error.message;
            } finally {
                confirmButton.disabled = false;
                confirmButton.textContent =
                    "Close Period";
            }
        }
    );
}
document.addEventListener("DOMContentLoaded", () => {
    const modal = document.getElementById("proofModal");
    const content = document.getElementById("proofModalContent");
    const download = document.getElementById("proofModalDownload");

    if (!modal || !content || !download) {
        return;
    }

    const imageTypes = /\.(png|jpe?g|gif|webp|bmp|svg)$/i;

    function renderProof(url) {
        const path = new URL(url, window.location.href).pathname;

        content.replaceChildren();

        if (imageTypes.test(path)) {
            const image = document.createElement("img");
            image.src = url;
            image.alt = "Payment proof";
            content.append(image);
        } else if (/\.pdf$/i.test(path)) {
            const frame = document.createElement("iframe");
            frame.src = url;
            frame.title = "Payment proof";
            content.append(frame);
        } else {
            const message = document.createElement("p");
            message.textContent =
                "This file type can't be previewed here. Use Download to open it.";
            content.append(message);
        }

        download.href = url;
    }

    // Any "View proof" link opens in the in-page viewer instead of a new tab.
    document.addEventListener("click", (event) => {
        const link = event.target.closest("a[data-proof-viewer]");

        if (
            !link ||
            event.ctrlKey ||
            event.metaKey ||
            event.shiftKey
        ) {
            return;
        }

        event.preventDefault();
        renderProof(link.href);
        modal.showModal();
    });

    modal
        .querySelectorAll("[data-proof-close]")
        .forEach((button) => {
            button.addEventListener("click", () => modal.close());
        });

    // Clicking the dimmed backdrop closes the viewer.
    modal.addEventListener("click", (event) => {
        if (event.target === modal) {
            modal.close();
        }
    });

    modal.addEventListener("close", () => {
        content.replaceChildren();
    });
});

function formatMoney(amount) {
    // Mirrors the server's `money` filter: "29,700.00 (29.7k)".
    const grouped = amount.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
    const abs = Math.abs(amount);
    let suffix = "";

    if (abs >= 10000000) {
        suffix = `${(abs / 10000000).toFixed(2)} CR`;
    } else if (abs >= 100000) {
        suffix = `${(abs / 100000).toFixed(1)} Lakh`;
    } else if (abs >= 1000) {
        suffix = `${(abs / 1000).toFixed(1)}k`;
    }

    return suffix ? `${grouped} (${suffix})` : grouped;
}

// Owner's purchase order: re-price each line as its discount % changes.
// Works in whole paisa so the running totals don't drift.
document.addEventListener("DOMContentLoaded", () => {
    document
        .querySelectorAll("[data-live-pricing]")
        .forEach((form) => {
            const taxPercentage = parseFloat(form.dataset.tax || "0");
            const shippingPaisa = Math.round(
                parseFloat(form.dataset.shipping || "0") * 100
            );

            function recompute() {
                let subtotalPaisa = 0;

                form
                    .querySelectorAll("[data-price-row]")
                    .forEach((row) => {
                        const input = row.querySelector(".discount-input");
                        let discount = parseFloat(
                            input ? input.value : row.dataset.discount
                        );

                        if (Number.isNaN(discount)) {
                            discount = 0;
                        }

                        discount = Math.min(Math.max(discount, 0), 100);

                        const listPaisa = Math.round(
                            parseFloat(row.dataset.listPrice) * 100
                        );
                        const unitPaisa = Math.round(
                            (listPaisa * (100 - discount)) / 100
                        );
                        const linePaisa = Math.round(
                            unitPaisa * parseFloat(row.dataset.quantity)
                        );

                        row.querySelector("[data-unit-price]").textContent =
                            formatMoney(unitPaisa / 100);
                        row.querySelector("[data-line-total]").textContent =
                            formatMoney(linePaisa / 100);

                        subtotalPaisa += linePaisa;
                    });

                const taxPaisa = Math.round(
                    (subtotalPaisa * taxPercentage) / 100
                );

                form.querySelector("[data-subtotal]").textContent =
                    formatMoney(subtotalPaisa / 100);
                form.querySelector("[data-grand-total]").textContent =
                    formatMoney(
                        (subtotalPaisa + taxPaisa + shippingPaisa) / 100
                    );
            }

            form.addEventListener("input", recompute);
        });
});


// Brand colour picker: drag the swatch or type a hex code — each keeps
// the other in step.
document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-color-picker]").forEach((picker) => {
        const swatch = picker.querySelector("[data-color-swatch]");
        const hex = picker.querySelector("[data-color-hex]");

        if (!swatch || !hex) {
            return;
        }

        swatch.addEventListener("input", () => {
            hex.value = swatch.value;
        });

        hex.addEventListener("input", () => {
            const value = hex.value.trim();
            if (/^#[0-9a-fA-F]{6}$/.test(value)) {
                swatch.value = value.toLowerCase();
            }
        });
    });
});

// Clickable table rows: a click anywhere on the row opens it, except on
// links, buttons and form controls inside it (they keep their own job).
document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("tr.row-link[data-href]").forEach((row) => {
        row.addEventListener("click", (event) => {
            if (event.target.closest("a, button, input, select, label")) {
                return;
            }

            if (window.getSelection()?.toString()) {
                return;
            }

            if (event.metaKey || event.ctrlKey) {
                window.open(row.dataset.href, "_blank");
            } else {
                window.location.href = row.dataset.href;
            }
        });
    });

    document.querySelectorAll("form[data-autosubmit]").forEach((form) => {
        form.addEventListener("change", () => form.requestSubmit());
    });

    // Product form: picking a category pre-selects its unit of measure.
    document.querySelectorAll("[data-unit-by-category]").forEach((select) => {
        const units = JSON.parse(select.dataset.unitByCategory || "{}");
        const unitSelect = select.form?.querySelector("[name=unit_of_measure]");

        select.addEventListener("change", () => {
            const unit = units[select.value];
            if (unit && unitSelect) {
                unitSelect.value = unit;
            }
        });
    });
});

// "Select all" checkbox: data-select-all="<name>" ticks every
// checkbox with that name in the same form.
document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-select-all]").forEach((toggle) => {
        const boxes = () =>
            toggle.form?.querySelectorAll(
                `input[type=checkbox][name="${toggle.dataset.selectAll}"]`
            ) || [];

        toggle.addEventListener("change", () => {
            boxes().forEach((box) => {
                box.checked = toggle.checked;
            });
        });
    });
});

// Product photo gallery: clicking a thumbnail shows it as the main image.
document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-gallery]").forEach((gallery) => {
        const main = gallery.querySelector("[data-gallery-main]");

        gallery.querySelectorAll("[data-gallery-src]").forEach((thumb) => {
            thumb.addEventListener("click", () => {
                if (main) {
                    main.src = thumb.dataset.gallerySrc;
                }
                gallery
                    .querySelectorAll(".gallery-thumb")
                    .forEach((other) => other.classList.toggle("is-active", other === thumb));
            });
        });
    });
});

// Product catalog selection and the unified product editor.
document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-product-selection]").forEach((form) => {
        const boxes = [...form.querySelectorAll('input[name="products"]')];
        const toggle = form.querySelector("[data-select-all]");
        function update() {
            const count = boxes.filter(box => box.checked).length;
            form.querySelector("[data-selection-count]").textContent = count
                ? `${count} selected` : "Select products to manage them together";
            form.querySelectorAll("[data-selection-action]").forEach(button => button.disabled = count === 0);
            toggle.checked = count > 0 && count === boxes.length;
            toggle.indeterminate = count > 0 && count < boxes.length;
        }
        form.addEventListener("change", update);
        update();
        // Restore accurate selection after browser back/forward navigation.
        window.addEventListener("pageshow", update);
    });

    const form = document.querySelector("[data-product-editor]");
    if (!form) return;
    let dirty = false;
    let submitting = false;
    function markDirty() {
        dirty = true;
        const state = form.querySelector("[data-save-state]");
        state.textContent = "Unsaved changes";
        state.classList.add("is-dirty");
    }
    form.addEventListener("input", markDirty);
    form.addEventListener("change", markDirty);
    form.addEventListener("submit", () => { submitting = true; });
    window.addEventListener("beforeunload", event => {
        if (dirty && !submitting) {
            event.preventDefault();
            event.returnValue = "";
        }
    });
    function openDetails(element) {
        let detail = element.closest("details");
        while (detail) {
            detail.open = true;
            detail = detail.parentElement.closest("details");
        }
    }
    form.addEventListener("invalid", event => openDetails(event.target), true);
    form.querySelectorAll(".field-error, .errorlist").forEach(openDetails);

    const status = form.querySelector('[name="active"]');
    status?.addEventListener("change", () => {
        const badge = form.querySelector("[data-status-badge]");
        if (!badge) return;
        const active = status.value !== "False";
        badge.textContent = active ? "Active" : "On hold";
        badge.className = `pc-status ${active ? "pc-status-active" : "pc-status-hold"}`;
    });

    const rich = form.querySelector("[data-rich-editor]");
    if (rich) {
        const source = rich.querySelector("textarea");
        const visual = rich.querySelector("[data-rich-visual]");
        const toolbar = rich.querySelector("[data-rich-toolbar]");
        const sourceButton = rich.querySelector("[data-rich-source]");
        let sourceMode = false;
        source.hidden = true;
        visual.hidden = false;
        toolbar.hidden = false;
        const allowed = new Set(["P", "DIV", "SPAN", "BR", "STRONG", "B", "EM", "I", "U", "S", "UL", "OL", "LI", "H2", "H3", "H4", "BLOCKQUOTE", "A", "TABLE", "THEAD", "TBODY", "TR", "TH", "TD"]);
        const blocked = new Set(["SCRIPT", "STYLE", "IFRAME", "OBJECT", "SVG", "MATH", "TEMPLATE"]);
        function safeHtml(value) {
            const parsed = new DOMParser().parseFromString(value, "text/html");
            const output = document.createElement("div");
            function copy(node, parent) {
                if (node.nodeType === Node.TEXT_NODE) {
                    parent.append(document.createTextNode(node.textContent));
                    return;
                }
                if (node.nodeType !== Node.ELEMENT_NODE || blocked.has(node.tagName)) return;
                let next = parent;
                if (allowed.has(node.tagName)) {
                    next = document.createElement(node.tagName.toLowerCase());
                    if (node.tagName === "A") {
                        const href = node.getAttribute("href") || "";
                        try {
                            if (href && ["https:", "http:", "mailto:"].includes(new URL(href, location.href).protocol)) {
                                next.setAttribute("href", href);
                                next.setAttribute("rel", "noopener noreferrer");
                            }
                        } catch (_) { /* Keep the link text when its URL is invalid. */ }
                    }
                    parent.append(next);
                }
                node.childNodes.forEach(child => copy(child, next));
            }
            parsed.body.childNodes.forEach(node => copy(node, output));
            return output.innerHTML;
        }
        const sync = () => { if (!sourceMode) source.value = visual.innerHTML; };
        visual.addEventListener("input", sync);
        visual.addEventListener("paste", event => {
            event.preventDefault();
            const html = event.clipboardData.getData("text/html");
            if (html) document.execCommand("insertHTML", false, safeHtml(html));
            else document.execCommand("insertText", false, event.clipboardData.getData("text/plain"));
            sync();
            markDirty();
        });
        toolbar.querySelectorAll("[data-rich-command]").forEach(button => {
            button.addEventListener("mousedown", event => event.preventDefault());
            button.addEventListener("click", () => {
                if (sourceMode) return;
                visual.focus();
                document.execCommand(button.dataset.richCommand, false, null);
                sync();
                markDirty();
            });
        });
        sourceButton.addEventListener("click", () => {
            if (sourceMode) {
                visual.innerHTML = safeHtml(source.value);
                sourceMode = false;
                sync();
            } else {
                sync();
                sourceMode = true;
            }
            visual.hidden = sourceMode;
            source.hidden = !sourceMode;
            sourceButton.setAttribute("aria-pressed", String(sourceMode));
            toolbar.querySelectorAll("[data-rich-command]").forEach(button => button.disabled = sourceMode);
            (sourceMode ? source : visual).focus();
        });
        rich.querySelector("label").addEventListener("click", event => {
            if (!sourceMode) { event.preventDefault(); visual.focus(); }
        });
        form.addEventListener("submit", sync);
    }

    const preview = form.querySelector("[data-media-preview]");
    const objectUrls = [];
    for (const name of ["image", "gallery_photos"]) {
        const input = form.querySelector(`input[type="file"][name="${name}"]`);
        input?.addEventListener("change", () => {
            preview.querySelectorAll(`[data-client-preview="${name}"]`).forEach(item => item.remove());
            if (name === "image" && input.files.length) preview.querySelector(".pc-media-main")?.remove();
            [...input.files].filter(file => file.type.startsWith("image/")).forEach(file => {
                const tile = document.createElement("div");
                tile.className = `pc-media-item${name === "image" ? " pc-media-main" : ""}`;
                tile.dataset.clientPreview = name;
                const image = document.createElement("img");
                const url = URL.createObjectURL(file);
                objectUrls.push(url);
                image.src = url;
                image.alt = file.name;
                tile.append(image);
                if (name === "image") preview.prepend(tile);
                else preview.insertBefore(tile, preview.querySelector(".pc-media-add"));
            });
        });
    }
    window.addEventListener("pagehide", () => objectUrls.forEach(url => URL.revokeObjectURL(url)));
});
