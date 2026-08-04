/* global React, Logo */

const Footer = () => (
  <footer className="l-footer">
    <div className="l-footer-left">
      <Logo />
      <span>© 2026 Mzansi Agentive (Pty) Ltd</span>
      <span className="l-footer-tag">Open Source · AGPL-3.0</span>
    </div>
    <div className="l-footer-right">
      <a>GitHub</a>
      <a>Docs</a>
      <a>Status</a>
      <a>Privacy</a>
      <a>Terms</a>
    </div>
  </footer>
);

window.Footer = Footer;
