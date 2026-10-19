import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [sveltekit()],
	server: {
		port: 5173,
		// console-api has no CORS headers (by design — /admin/v1 is meant to sit behind the
		// same VPN ingress as web in staging/production, PRD §11, never called cross-origin
		// from a browser there). Locally the two run on different ports, so proxy same-origin
		// requests through instead of adding CORS-handling code that would only ever run here.
		// Pairs with PUBLIC_CONSOLE_API_BASE_URL=/admin/v1 in web/.env for local dev.
		proxy: {
			'/admin/v1': {
				target: 'http://127.0.0.1:8000',
				changeOrigin: true
			},
			// ai-runtime's Playground surface (PRD §9.3) - the browser calls this directly
			// with a runtime token, same proxy-locally reasoning as /admin/v1 above. Pairs
			// with PUBLIC_AI_RUNTIME_BASE_URL=/ai/v1 in web/.env.
			'/ai/v1': {
				target: 'http://127.0.0.1:8080',
				changeOrigin: true
			}
		}
	}
});
