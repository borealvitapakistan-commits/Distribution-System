document.addEventListener("DOMContentLoaded", () => {
    const form = document.querySelector("[data-placement-form]");
    if (!form) return;
    const rows = form.querySelector("[data-placement-rows]");
    const template = form.querySelector("[data-placement-template]");
    const management = form.querySelector("[name='placements-TOTAL_FORMS']");
    const addButton = form.querySelector("[data-add-destination]");
    const submit = form.querySelector("[data-place-submit]");
    const review = form.querySelector(".iw-placement-review");
    const plan = form.querySelector("[data-placement-plan]");
    const feedback = form.querySelector("[data-placement-feedback]");
    const breakdown = form.querySelector("[data-placement-breakdown]");

    // Integer arithmetic keeps stock quantities exact to four decimal places.
    const units = value => {
        if (!/^(?:\d+(?:\.\d{0,4})?|\.\d{1,4})$/.test(String(value))) return 0n;
        const [whole, decimal = ""] = String(value).split(".");
        return BigInt(whole || "0") * 10000n + BigInt(decimal.padEnd(4, "0"));
    };
    const plain = value => {
        const fraction = String(value % 10000n).padStart(4, "0").replace(/0+$/, "");
        return String(value / 10000n) + (fraction ? "." + fraction : "");
    };
    const pretty = value => {
        const [whole, fraction] = plain(value).split(".");
        return BigInt(whole).toLocaleString() + (fraction ? "." + fraction : "");
    };
    const count = value => `${pretty(value)} ${value === 10000n ? "unit" : "units"}`;
    const available = units(form.dataset.available);
    const activeRows = () => [...rows.querySelectorAll("[data-placement-row]")].filter(row => !row.hidden);
    const quantityInput = row => row.querySelector("[data-placement-quantity]");
    const rowQuantity = row => units(quantityInput(row).value);
    const destination = row => row.querySelector("[data-placement-destination]:checked");
    const totalQuantity = () => activeRows().reduce((sum, row) => sum + rowQuantity(row), 0n);

    function initializeRow(row) {
        row.querySelectorAll("[data-warehouse-search-wrap]").forEach(wrap => { wrap.hidden = false; });
        const amount = row.querySelector("[data-quantity-choice]");
        if (rowQuantity(row) !== available || quantityInput(row).value === "") amount.open = true;
        if (row.querySelector("[name$='-DELETE']")?.checked) {
            row.hidden = true;
            row.querySelectorAll("input").forEach(input => {
                if (!input.name.endsWith("-DELETE")) input.disabled = true;
            });
        }
    }

    function refresh() {
        const active = activeRows();
        const total = totalQuantity();
        const over = total > available;
        const missingWarehouse = active.findIndex(row => !destination(row));
        const invalidAmount = active.findIndex(row => rowQuantity(row) <= 0n || !quantityInput(row).checkValidity());
        const selected = active.map(destination).filter(Boolean);
        const duplicate = new Set(selected.map(input => input.value)).size !== selected.length;
        const ready = missingWarehouse === -1 && invalidAmount === -1 && !over && !duplicate;
        review.classList.toggle("is-over-limit", over || duplicate);
        review.classList.toggle("is-ready", ready);
        submit.disabled = !ready || Boolean(form.dataset.submitting);

        active.forEach((row, index) => {
            const split = active.length > 1;
            const wasSplit = row.classList.contains("is-split-row");
            row.classList.toggle("is-split-row", split);
            row.querySelector("[data-row-heading]").hidden = !split;
            row.querySelector("[data-row-label]").textContent = `Warehouse ${index + 1}`;
            row.querySelector("[data-remove-destination]").hidden = !split;
            const amount = row.querySelector("[data-quantity-choice]");
            if (split) amount.open = true;
            else if (wasSplit && rowQuantity(row) === available) amount.open = false;
            row.querySelector("[data-amount-label]").textContent = !split && rowQuantity(row) === available
                ? `All ${count(available)}` : `Move ${count(rowQuantity(row))}`;
            row.querySelectorAll("[data-placement-destination]").forEach(input => {
                input.closest(".iw-warehouse-option").classList.toggle("is-selected", input.checked);
            });
            const remaining = available - (total - rowQuantity(row));
            const useRemaining = row.querySelector("[data-use-remaining]");
            useRemaining.hidden = false;
            useRemaining.disabled = remaining <= 0n;
            useRemaining.textContent = split
                ? `Use remaining ${pretty(remaining > 0n ? remaining : 0n)}`
                : `Use all ${pretty(available)}`;
        });
        addButton.hidden = active.length === 1 && missingWarehouse !== -1;
        addButton.disabled = active.length >= 25;
        addButton.textContent = active.length > 1 ? "+ Add another warehouse" : "+ Use more than one warehouse";
        breakdown.replaceChildren();
        breakdown.hidden = true;

        if (missingWarehouse !== -1) {
            plan.textContent = active.length === 1 ? "Choose a warehouse above." : `Choose warehouse ${missingWarehouse + 1} above.`;
            feedback.textContent = active.length === 1
                ? invalidAmount === -1 ? `${count(total)} ${total === 10000n ? "is" : "are"} selected. You can change the amount if needed.`
                : "Enter an amount and choose its warehouse."
                : "Choose where each part of the batch should go.";
        } else if (invalidAmount !== -1) {
            plan.textContent = active.length === 1 ? "Enter how many units you want to move." : `Enter a quantity for warehouse ${invalidAmount + 1}.`;
            feedback.textContent = `This batch has ${count(available)} available.`;
        } else if (over) {
            plan.textContent = "The amounts add up to more than this batch contains.";
            feedback.textContent = `You have ${count(available)}. Reduce the total by ${count(total - available)}.`;
        } else if (duplicate) {
            plan.textContent = "Choose a different warehouse for each part.";
            feedback.textContent = "Combine quantities for the same warehouse in one entry.";
        } else if (active.length === 1) {
            const chosen = selected[0];
            const all = total === available;
            plan.textContent = chosen.dataset.kind === "region"
                ? `${count(total)} will be assigned to ${chosen.dataset.name} for now.`
                : `${all ? "All " : ""}${count(total)} will go to ${chosen.dataset.name}.`;
            feedback.textContent = total < available
                ? `${count(available - total)} will still need a warehouse.`
                : chosen.dataset.kind === "region" ? "You can choose its warehouse later from the region page."
                : "The stock will appear in this warehouse after you confirm.";
        } else {
            const warehousesOnly = selected.every(input => input.dataset.kind === "warehouse");
            plan.textContent = `${count(total)} will go to ${active.length} ${warehousesOnly ? "warehouses" : "places"}.`;
            active.forEach(row => {
                const chosen = destination(row);
                const item = document.createElement("li");
                const amount = document.createElement("strong");
                amount.textContent = count(rowQuantity(row));
                item.append(amount, document.createTextNode(` → ${chosen.dataset.name}${chosen.dataset.kind === "region" ? " (warehouse later)" : ""}`));
                breakdown.append(item);
            });
            breakdown.hidden = false;
            feedback.textContent = total < available
                ? `${count(available - total)} will still need a warehouse.` : "The whole batch will be moved.";
        }
        if (!form.dataset.submitting) {
            const assignOnly = selected.length > 0 && selected.every(input => input.dataset.kind === "region");
            submit.textContent = ready ? `${assignOnly ? "Assign" : "Move"} ${count(total)}` : "Move stock";
        }
    }

    rows.querySelectorAll("[data-placement-row]").forEach(initializeRow);
    rows.addEventListener("input", event => {
        const search = event.target.closest("[data-warehouse-search]");
        if (search) {
            const row = search.closest("[data-placement-row]");
            const term = search.value.trim().toLocaleLowerCase();
            let matches = 0;
            row.querySelectorAll("[data-warehouse-option]").forEach(option => {
                option.hidden = !option.dataset.search.toLocaleLowerCase().includes(term);
                if (!option.hidden) matches += 1;
            });
            row.querySelector("[data-no-warehouse-match]").hidden = matches > 0;
        }
        refresh();
    });
    rows.addEventListener("change", refresh);
    rows.addEventListener("click", event => {
        const row = event.target.closest("[data-placement-row]");
        if (!row) return;
        const amountSummary = event.target.closest("[data-quantity-choice] > summary");
        if (amountSummary && !amountSummary.parentElement.open) {
            requestAnimationFrame(() => {
                if (!amountSummary.parentElement.open) return;
                quantityInput(row).focus();
                quantityInput(row).select();
            });
        }
        if (event.target.closest("[data-use-remaining]")) {
            const remaining = available - (totalQuantity() - rowQuantity(row));
            quantityInput(row).value = plain(remaining > 0n ? remaining : 0n);
            quantityInput(row).focus();
            refresh();
        }
        if (event.target.closest("[data-remove-destination]") && activeRows().length > 1) {
            row.querySelector("[name$='-DELETE']").checked = true;
            row.hidden = true;
            row.querySelectorAll("input").forEach(input => {
                if (!input.name.endsWith("-DELETE")) input.disabled = true;
            });
            const remaining = activeRows();
            if (remaining.length === 1 && quantityInput(remaining[0]).value === "") {
                quantityInput(remaining[0]).value = plain(available);
            }
            remaining.at(-1)?.querySelector("[data-placement-destination]").focus();
            refresh();
        }
    });
    addButton.addEventListener("click", () => {
        const active = activeRows();
        if (active.length >= 25) return;
        const firstWasAll = active.length === 1 && rowQuantity(active[0]) === available;
        if (firstWasAll) quantityInput(active[0]).value = "";
        const leftover = available - totalQuantity();
        let row = rows.querySelector("[data-placement-row][hidden]");
        if (row) {
            row.hidden = false;
            row.querySelectorAll("input").forEach(input => {
                input.disabled = false;
                if (["checkbox", "radio"].includes(input.type)) input.checked = false;
                else input.value = "";
            });
            row.querySelectorAll("[data-warehouse-option]").forEach(option => { option.hidden = false; });
            row.querySelector("[data-no-warehouse-match]").hidden = true;
        } else {
            const index = Number(management.value);
            const holder = document.createElement("template");
            holder.innerHTML = template.innerHTML.replaceAll("__prefix__", String(index));
            row = holder.content.firstElementChild;
            rows.append(row);
            management.value = String(index + 1);
            initializeRow(row);
        }
        quantityInput(row).value = !firstWasAll && leftover > 0n ? plain(leftover) : "";
        refresh();
        if (firstWasAll) quantityInput(active[0]).focus();
        else row.querySelector("[data-placement-destination]")?.focus();
    });
    form.addEventListener("submit", event => {
        refresh();
        if (form.dataset.submitting || submit.disabled || !form.checkValidity()) {
            event.preventDefault();
            return;
        }
        form.dataset.submitting = "true";
        submit.disabled = true;
        submit.textContent = "Moving stock…";
    });
    window.addEventListener("pageshow", () => { delete form.dataset.submitting; refresh(); });
    refresh();
});
