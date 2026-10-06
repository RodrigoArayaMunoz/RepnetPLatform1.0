import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import DownloadPublications from "../../src/frontend/pages/DownloadPublications.jsx";
import SideBar from "../../src/frontend/components/SideBar.jsx";
import TopMenuBar from "../../src/frontend/components/TopMenuBar.jsx";
import "../../src/frontend/styles/MainLayout.css";

function Preview() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  return (
    <MemoryRouter initialEntries={["/gestion/descargar-publicaciones"]}>
      <div className="layout">
        <SideBar sidebarOpen={sidebarOpen} setSidebarOpen={setSidebarOpen} />
        <main className="layout__main">
          <TopMenuBar onOpenSidebar={() => setSidebarOpen(true)} />
          <div className="layout__content">
            <Routes>
              <Route path="/gestion/descargar-publicaciones" element={<DownloadPublications authUserId="preview-user" />} />
              <Route path="*" element={<p>Otra pantalla de la vista previa</p>} />
            </Routes>
          </div>
        </main>
      </div>
    </MemoryRouter>
  );
}

createRoot(document.getElementById("root")).render(<Preview />);
