/* global React, Logo, Button, Icon */
// Top nav — transparent over hero, sticky after scroll.

const Nav = () => {
  return (
    <nav className="top-nav">
      <Logo />
      <div className="nav-links">
        <a className="nav-link">Product</a>
        <a className="nav-link">How it works</a>
        <a className="nav-link">Pricing</a>
        <a className="nav-link">Docs</a>
        <a className="nav-link">Changelog</a>
      </div>
      <div className="nav-right">
        <Button variant="ghost" size="sm">Sign in</Button>
        <Button variant="primary" size="sm">
          Start free
          <Icon name="arrow-right" size={14} strokeWidth={2.5} />
        </Button>
      </div>
    </nav>
  );
};

window.Nav = Nav;
