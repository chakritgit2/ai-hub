import type { Config } from 'tailwindcss';

export default {
	content: ['./src/**/*.{html,js,svelte,ts}'],
	theme: {
		extend: {
			colors: {
				bg: '#F4F3EF',
				sidebar: '#16181B',
				primary: {
					DEFAULT: '#0E6B66',
					50: '#E6F2F1',
					100: '#CCE5E3',
					500: '#0E6B66',
					600: '#0C5B57',
					700: '#0A4A47'
				},
				warning: {
					DEFAULT: '#F59E0B',
					dark: '#B45309'
				},
				danger: {
					DEFAULT: '#DC2626',
					dark: '#991B1B'
				}
			},
			fontFamily: {
				sans: ['IBM Plex Sans Thai', 'ui-sans-serif', 'system-ui', 'sans-serif'],
				mono: ['IBM Plex Mono', 'ui-monospace', 'SFMono-Regular', 'monospace']
			}
		}
	},
	plugins: []
} satisfies Config;
