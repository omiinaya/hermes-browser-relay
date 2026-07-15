import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import Link from '@docusaurus/Link';

function HomepageHeader() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <header className="hero">
      <div className="container">
        <h1 className="hero__title">{siteConfig.title}</h1>
        <p className="hero__subtitle">{siteConfig.tagline}</p>
        <div style={{display: 'flex', gap: '12px', justifyContent: 'center', flexWrap: 'wrap'}}>
          <Link
            className="button button--primary button--lg"
            to="/getting-started">
            Get Started
          </Link>
          <Link
            className="button button--secondary button--lg"
            to="/tools">
            View All 46 Tools
          </Link>
          <Link
            className="button button--secondary button--lg"
            to="/architecture">
            Architecture
          </Link>
        </div>
      </div>
    </header>
  );
}

function Feature({title, description, link}: {title: string; description: string; link: string}) {
  return (
    <div style={{padding: '1rem', flex: '1 1 280px', maxWidth: '340px'}}>
      <h3>
        <Link to={link}>{title}</Link>
      </h3>
      <p>{description}</p>
    </div>
  );
}

const features = [
  {
    title: '📋 46 Browser Tools',
    description: 'Navigate, click, type, screenshot, scrape, fill forms, manage tabs — full browser automation via Hermes agent tools.',
    link: '/tools',
  },
  {
    title: '⚡ Content Script Fast Path',
    description: 'DOM operations run directly in the page via window.__hermes — no CDP overhead. ~10x faster for click/type/snapshot.',
    link: '/architecture',
  },
  {
    title: '🔌 Plugin + MCP + CLI',
    description: 'Use as a Hermes plugin, an MCP stdio server, or the cdp.py CLI — same 46 tools, three interfaces.',
    link: '/getting-started',
  },
  {
    title: '📸 Screenshot + Annotated',
    description: 'Capture page screenshots with numbered overlay labels on interactive elements. Ideal for QA and debugging.',
    link: '/tools#media',
  },
  {
    title: '🌐 Multi-Tab Routing',
    description: 'All tools accept an optional tab_id. Attach to multiple tabs, switch between them, manage tab lifecycle.',
    link: '/tools#tabs',
  },
  {
    title: '🐳 Docker Ready',
    description: 'Multi-stage Dockerfile with health checks. Run the relay in a container for isolated deployment.',
    link: '/docker',
  },
];

export default function Home(): JSX.Element {
  return (
    <Layout
      title="browser-relay — Remote Browser Control"
      description="Remote Chrome/Chromium browser control via CDP. Hermes Agent plugin & MCP server — 46 browser automation tools.">
      <HomepageHeader />
      <main>
        <div style={{display: 'flex', flexWrap: 'wrap', justifyContent: 'center', maxWidth: '1100px', margin: '2rem auto 4rem'}}>
          {features.map((f, i) => (
            <Feature key={i} {...f} />
          ))}
        </div>
      </main>
    </Layout>
  );
}
