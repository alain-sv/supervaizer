const { test, expect } = require('@playwright/test');

test.describe('Agents Page Display Tests', () => {
    let apiKey = 'test-api-key';

    test.beforeEach(async ({ page }) => {
        // Set up API key for admin access if required
        await page.setExtraHTTPHeaders({
            'X-API-Key': apiKey
        });
    });

    test('should display agents page with proper card layout', async ({ page }) => {
        // Navigate to agents page
        await page.goto('http://localhost:8000/admin/agents');

        // Wait for page to load
        await page.waitForLoadState('networkidle');

        // Check if page title is correct
        await expect(page).toHaveTitle(/Agents.*Supervaizer Admin/);

        // Check if main heading exists
        await expect(page.locator('h2:has-text("Agents")')).toBeVisible();

        // Check if subtitle exists  
        await expect(page.locator('text=Manage AI agents and their configurations')).toBeVisible();

        // Check if refresh button exists
        await expect(page.locator('button:has-text("Refresh")')).toBeVisible();

        // Check if new agent button exists
        await expect(page.locator('button:has-text("New Agent")')).toBeVisible();
    });

    test('should display agent cards with proper structure', async ({ page }) => {
        // Navigate to agents page
        await page.goto('http://localhost:8000/admin/agents');

        // Wait for page to load
        await page.waitForLoadState('networkidle');

        // Check if agents grid container exists
        const agentsContainer = page.locator('#agents-table-container');
        await expect(agentsContainer).toBeVisible();

        // Check for either agent cards or empty state
        const agentCards = page.locator('.grid .bg-white.border');
        const emptyState = page.locator('text=No agents available');

        // At least one should be visible
        const hasCards = await agentCards.count() > 0;
        const hasEmptyState = await emptyState.isVisible();

        expect(hasCards || hasEmptyState).toBeTruthy();

        if (hasCards) {
            // If we have agent cards, check their structure
            const firstCard = agentCards.first();

            // Check card has proper structure
            await expect(firstCard.locator('h3')).toBeVisible(); // Agent name
            await expect(firstCard.locator('p').first()).toBeVisible(); // Agent type
            await expect(firstCard.locator('button:has-text("View")')).toBeVisible();
            await expect(firstCard.locator('button:has-text("Configure")')).toBeVisible();

            // Check for icon container
            await expect(firstCard.locator('div.w-10.h-10')).toBeVisible();

            // Check for status badge
            await expect(firstCard.locator('span:has-text("Active")')).toBeVisible();

            console.log(`Found ${await agentCards.count()} agent cards`);
        } else if (hasEmptyState) {
            // Check empty state structure
            await expect(page.locator('svg.mx-auto')).toBeVisible(); // Empty state icon
            await expect(page.locator('h3:has-text("No agents available")')).toBeVisible();
            console.log('Empty state displayed correctly');
        }
    });

    test('should handle refresh functionality', async ({ page }) => {
        // Navigate to agents page
        await page.goto('http://localhost:8000/admin/agents');

        // Wait for page to load
        await page.waitForLoadState('networkidle');

        // Click refresh button
        const refreshButton = page.locator('button:has-text("Refresh")');
        await expect(refreshButton).toBeVisible();

        // Watch for network request
        const responsePromise = page.waitForResponse('/admin/api/agents');
        await refreshButton.click();

        // Wait for the response
        const response = await responsePromise;
        expect(response.status()).toBe(200);

        console.log('Refresh functionality working');
    });

    test('should have responsive grid layout', async ({ page }) => {
        // Test desktop layout
        await page.setViewportSize({ width: 1200, height: 800 });
        await page.goto('http://localhost:8000/admin/agents');
        await page.waitForLoadState('networkidle');

        const grid = page.locator('.grid.grid-cols-1');
        await expect(grid).toBeVisible();

        // Check if grid has responsive classes
        const gridClasses = await grid.getAttribute('class');
        expect(gridClasses).toContain('lg:grid-cols-3');
        expect(gridClasses).toContain('sm:grid-cols-2');

        console.log('Responsive grid layout verified');
    });

    test('should display filters section', async ({ page }) => {
        await page.goto('http://localhost:8000/admin/agents');
        await page.waitForLoadState('networkidle');

        // Check filters section exists
        await expect(page.locator('button:has-text("Filters")')).toBeVisible();

        // Click to open filters
        await page.click('button:has-text("Filters")');

        // Check filter controls
        await expect(page.locator('select[name="status"]')).toBeVisible();
        await expect(page.locator('select[name="agent_type"]')).toBeVisible();
        await expect(page.locator('input[name="search"]')).toBeVisible();
        await expect(page.locator('select[name="sort"]')).toBeVisible();

        console.log('Filters section working correctly');
    });
}); 