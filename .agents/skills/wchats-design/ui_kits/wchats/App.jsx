/* global React, ReactDOM, Nav, Hero, HowItWorks, ShipSection, Footer */

const App = () => (
  <>
    <Nav />
    <Hero />
    <HowItWorks />
    <ShipSection />
    <Footer />
  </>
);

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
