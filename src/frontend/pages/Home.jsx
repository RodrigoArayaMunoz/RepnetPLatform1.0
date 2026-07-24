import logo from "../../assets/repnetsolo_logo.png";
import "../styles/Home.css";

export default function Home() {
  return (
    <section className="home-page">
      <div className="home-page__layout">
        <img src={logo} alt="Repnet" className="home-page__logo" />
      </div>
    </section>
  );
}