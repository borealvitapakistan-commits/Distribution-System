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
});


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