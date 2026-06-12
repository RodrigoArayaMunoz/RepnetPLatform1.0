import { useRef, useState } from "react";
import "../styles/CompatibilitiesUpload.css";

const FAMILY_OPTIONS = [
  "Bandejas",
  "Pastillas de freno",
  "Discos de freno",
  "Faroles",
  "Luces",
];

function UploadFamily() {
  const fileInputRef = useRef(null);
  const [selectedFamily, setSelectedFamily] = useState("");
  const [file, setFile] = useState(null);

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (event) => {
    const selectedFile = event.target.files?.[0] ?? null;
    setFile(selectedFile);
  };

  const handleExecuteUpload = () => {
    // Pendiente de conectar con el servicio de carga de familias.
  };

  return (
    <section className="compat-page">
      <div className="compat-upload-layout upload-family-layout">
        <div className="file-wrapper">
          <label className="family-label" htmlFor="familySelect">
            Familia
          </label>

          <select
            id="familySelect"
            className="family-select"
            value={selectedFamily}
            onChange={(event) => setSelectedFamily(event.target.value)}
          >
            <option value="">Seleccionar Familia</option>
            {FAMILY_OPTIONS.map((family) => (
              <option key={family} value={family}>
                {family}
              </option>
            ))}
          </select>
        </div>

        <div className="file-wrapper">
          <input
            ref={fileInputRef}
            className="file-input"
            type="file"
            accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            onChange={handleFileChange}
          />

          <span className="file-name">
            {file ? file.name : "Ningún archivo seleccionado"}
          </span>
        </div>

        <div className="actions-row">
          <button
            className="process-button"
            type="button"
            onClick={handleUploadClick}
          >
            Cargar Excel de compatibilidades
          </button>

          <button
            className="process-button execute-family-button"
            type="button"
            onClick={handleExecuteUpload}
            disabled={!file}
          >
            Ejecutar Carga de Familias
          </button>
        </div>
      </div>
    </section>
  );
}

export default UploadFamily;
