import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'browser-relay',
  tagline: 'Remote Chrome/Chromium browser control via CDP — Hermes Agent plugin & MCP server',
  favicon: 'img/icon128.png',

  url: 'https://omiinaya.github.io',
  baseUrl: '/hermes-browser-relay/',

  organizationName: 'omiinaya',
  projectName: 'hermes-browser-relay',

  onBrokenLinks: 'throw',
  markdown: {
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          editUrl: 'https://github.com/omiinaya/hermes-browser-relay/edit/master/website/',
          routeBasePath: '/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    image: 'img/icon128.png',
    colorMode: {
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'browser-relay',
      logo: {
        alt: 'browser-relay logo',
        src: 'img/icon128.png',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'docsSidebar',
          position: 'left',
          label: 'Docs',
        },
        {
          href: 'https://github.com/omiinaya/hermes-browser-relay',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {label: 'Getting Started', to: '/getting-started'},
            {label: 'Tools', to: '/tools'},
            {label: 'HTTP API', to: '/api'},
            {label: 'CLI', to: '/cli'},
          ],
        },
        {
          title: 'Community',
          items: [
            {label: 'Hermes Agent', href: 'https://hermes-agent.nousresearch.com/'},
            {label: 'GitHub Issues', href: 'https://github.com/omiinaya/hermes-browser-relay/issues'},
          ],
        },
        {
          title: 'More',
          items: [
            {label: 'Changelog', to: '/changelog'},
            {label: 'GitHub', href: 'https://github.com/omiinaya/hermes-browser-relay'},
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} — browser-relay. MIT licensed.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['bash', 'python', 'javascript', 'json', 'yaml'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
