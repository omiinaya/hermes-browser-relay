import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  docsSidebar: [
    'getting-started',
    {
      type: 'category',
      label: 'Reference',
      items: ['architecture', 'tools', 'api', 'cli', 'docker', 'configuration'],
    },
    {
      type: 'category',
      label: 'Development',
      items: ['development', 'contributing', 'changelog'],
    },
  ],
};

export default sidebars;
