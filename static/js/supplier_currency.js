window.SupplierMoney = {
    format(values) {
        return ['USD', 'UZS'].map(code => `${Number(values?.[code] || 0).toLocaleString('en-US', {maximumFractionDigits: code === 'UZS' ? 2 : 10})} ${code}`).join(' + ');
    },
    received(entries, channel) {
        return (entries || []).reduce((totals, entry) => {
            if (!channel || entry.channel === channel) totals[entry.currency] += Number(entry.amount || 0);
            return totals;
        }, {USD: 0, UZS: 0});
    },
    items(items) {
        return items.reduce((totals, item) => {
            totals[item.cost_currency || 'USD'] += Number(item.native_cost_price ?? item.cost_price ?? 0) * Number(item.quantity || 0);
            return totals;
        }, {USD: 0, UZS: 0});
    },
    total(batch) {
        return {USD: Number(batch.native_total_usd ?? batch.total_amount ?? 0), UZS: Number(batch.native_total_uzs || 0)};
    },
    closed(batch) {
        const totals = this.total(batch);
        return {USD: totals.USD - Number(batch.native_debts.USD), UZS: totals.UZS - Number(batch.native_debts.UZS)};
    },
    preview(values, entries, rate, priority) {
        const debts = {USD: Number(values.USD), UZS: Number(values.UZS)};
        let valid = true;
        entries.forEach(entry => {
            let left = Number(entry.amount);
            if (!Number.isFinite(left) || left < 0) valid = false;
            [priority, priority === 'USD' ? 'UZS' : 'USD'].forEach(target => {
                const factor = entry.currency === target ? 1 : target === 'UZS' ? rate : 1 / rate;
                const applied = Math.min(debts[target], Math.max(0, left) * factor);
                debts[target] -= applied;
                left -= applied / factor;
            });
            if (left > (entry.currency === 'UZS' ? 0.01 : 0.00001)) valid = false;
        });
        return {debts, valid};
    }
};