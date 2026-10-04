(() => {
    'use strict';

    const tables = [
        {selector: '#productsTable', name: 2, primary: [3, 5]},
        {selector: '.sup-table', name: 0, primary: [3]},
        {selector: '#spTable', name: 2, primary: [3, 5]},
        {selector: '.sales-table', name: 1, primary: [4, 8], path: '/debt-sales'}
    ];

    function enhanceTable(table, config) {
        const labels = Array.from(table.tHead?.rows[0]?.cells || [], cell => cell.textContent.trim());
        const spans = [];
        table.classList.add('ui-mobile-table');
        for (const row of table.tBodies[0]?.rows || []) {
            const cells = Array.from(row.cells).filter(cell => !cell.classList.contains('ui-detail-toggle'));
            if (cells.some(cell => cell.colSpan > 1)) continue;
            let column = 0;
            for (const cell of cells) {
                while (spans[column] > 0) column++;
                if (!row.dataset.uiReady) {
                    cell.dataset.uiLabel = labels[column] || '';
                    cell.dataset.uiRole = column === config.name ? 'name' : config.primary.includes(column) ? 'primary' : 'detail';
                }
                spans[column] = cell.rowSpan;
                column++;
            }
            for (let index = 0; index < spans.length; index++) spans[index] = Math.max(0, (spans[index] || 0) - 1);
            if (row.dataset.uiReady) continue;
            row.dataset.uiReady = 'true';
            const toggleCell = row.insertCell();
            toggleCell.className = 'ui-detail-toggle';
            const button = document.createElement('button');
            button.type = 'button';
            button.setAttribute('aria-expanded', 'false');
            const label = document.documentElement.lang === 'ru' ? '\u041f\u043e\u0434\u0440\u043e\u0431\u043d\u0435\u0435' : 'Batafsil';
            button.innerHTML = '<i class="fas fa-chevron-down" aria-hidden="true"></i> ' + label;
            button.addEventListener('click', event => {
                event.stopPropagation();
                const expanded = row.classList.toggle('ui-row-expanded');
                button.setAttribute('aria-expanded', String(expanded));
            });
            toggleCell.append(button);
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        const menuIcons = {
            '/': 'house', '/sales': 'cart-shopping', '/sales-history': 'chart-line',
            '/pending-sales': 'clock', '/debt-sales': 'file-invoice-dollar',
            '/products': 'box', '/transfer': 'right-left', '/return-product': 'rotate-left',
            '/xarajatlar': 'wallet', '/hisobot': 'chart-column', '/customers': 'address-book',
            '/debts': 'coins', '/operations-history': 'list-check',
            '/add_product_session': 'plus', '/add_product': 'plus', '/suppliers': 'truck',
            '/stores': 'store', '/warehouses': 'warehouse', '/users': 'users',
            '/settings': 'gear', '/check_stock': 'clipboard-check',
            '/currency-rate': 'money-bill-transfer', '/hosting': 'server'
        };
        for (const link of document.querySelectorAll('.sidebar-menu a')) {
            const label = link.querySelector('span:not(.icon)')?.textContent.trim();
            if (label) {
                link.title = label;
                link.setAttribute('aria-label', label);
            }
            const iconName = menuIcons[link.getAttribute('href')];
            const container = link.querySelector('.icon');
            if (iconName && container) {
                const icon = document.createElement('i');
                icon.className = 'fas fa-' + iconName;
                icon.setAttribute('aria-hidden', 'true');
                container.replaceChildren(icon);
            }
        }
        for (const config of tables) {
            if (config.path && location.pathname !== config.path) continue;
            for (const table of document.querySelectorAll(config.selector)) {
                if (!table.tBodies.length) continue;
                enhanceTable(table, config);
                new MutationObserver(() => enhanceTable(table, config)).observe(table.tBodies[0], {childList: true});
            }
        }
    });
})();