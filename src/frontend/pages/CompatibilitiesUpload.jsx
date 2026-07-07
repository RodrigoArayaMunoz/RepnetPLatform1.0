import { useRef, useState } from "react";
import * as XLSX from "xlsx";
import "../styles/CompatibilitiesUpload.css";

const FAMILY_OPTIONS = [
  {
    label: "BOMBAS DE AGUA",
    code: "MLC-VEHICLE_WATER_PUMPS",
  },
  {
    label: "BOMBAS DE ACEITE",
    code: "MLC-VEHICLE_OIL_PUMPS",
  },
  {
    label: "RODAMIENTOS",
    code: "MLC-VEHICLE_CLUTCH_BEARINGS",
  },
  {
    label: "PASTILLAS DE FRENO",
    code: "MLC-VEHICLE_BRAKE_PADS",
  },
  {
    label: "DISCOS DE FRENO",
    code: "MLC-VEHICLE_BRAKE_DISCS",
  },
  {
    label: "TAMBOR DE FRENO",
    code: "MLC-VEHICLE_BRAKE_DRUMS",
  },
  {
    label: "Patines de Freno",
    code: "MLC-VEHICLE_DRUM_BRAKE_SHOES",
  },
  {
    label: "BANDEJAS",
    code: "MLC-VEHICLE_SUSPENSION_CONTROL_ARMS",
  },
  {
    label: "FAROL",
    code: "MLC-VEHICLE_TAIL_LIGHTS",
  },
  {
    label: "OPTICOS",
    code: "MLC-VEHICLE_HEADLIGHTS",
  },
];

const FAMILY_HEADER = "FAMILIA";

const normalizeHeader = (value) =>
  String(value ?? "")
    .trim()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toUpperCase();

const buildOutputFileName = (fileName) => {
  const cleanName = fileName.replace(/\.(xlsx|xls)$/i, "");
  return `${cleanName || "compatibilidades"}_familia.xlsx`;
};

function UploadFamily() {
  const fileInputRef = useRef(null);
  const [selectedFamily, setSelectedFamily] = useState("");
  const [file, setFile] = useState(null);
  const [status, setStatus] = useState("idle");
  const [message, setMessage] = useState("");
  const [isProcessing, setIsProcessing] = useState(false);

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (event) => {
    const selectedFile = event.target.files?.[0] ?? null;

    if (
      selectedFile &&
      !/\.(xlsx|xls)$/i.test(selectedFile.name)
    ) {
      setFile(null);
      setStatus("error");
      setMessage("Archivo no válido. Selecciona un Excel (.xlsx o .xls).");
      return;
    }

    setFile(selectedFile);
    setStatus("idle");
    setMessage("");
  };

  const handleExecuteUpload = async () => {
    if (!file) {
      setStatus("error");
      setMessage("Debes cargar un archivo Excel antes de ejecutar el proceso.");
      return;
    }

    if (!selectedFamily) {
      setStatus("error");
      setMessage("Debes seleccionar una familia antes de ejecutar el proceso.");
      return;
    }

    try {
      setIsProcessing(true);
      setStatus("processing");
      setMessage("Procesando archivo Excel...");

      const fileBuffer = await file.arrayBuffer();
      const workbook = XLSX.read(fileBuffer, {
        type: "array",
        cellDates: true,
      });

      const sheetName = workbook.SheetNames[0];
      const worksheet = workbook.Sheets[sheetName];

      if (!worksheet || !worksheet["!ref"]) {
        throw new Error("El archivo Excel no contiene hojas con datos.");
      }

      const range = XLSX.utils.decode_range(worksheet["!ref"]);
      const headerRowIndex = range.s.r;
      let familyColumnIndex = null;

      for (let columnIndex = range.s.c; columnIndex <= range.e.c; columnIndex += 1) {
        const cellAddress = XLSX.utils.encode_cell({
          r: headerRowIndex,
          c: columnIndex,
        });
        const cellValue = worksheet[cellAddress]?.v;

        if (normalizeHeader(cellValue) === FAMILY_HEADER) {
          familyColumnIndex = columnIndex;
          break;
        }
      }

      if (familyColumnIndex === null) {
        familyColumnIndex = range.e.c + 1;
        const headerAddress = XLSX.utils.encode_cell({
          r: headerRowIndex,
          c: familyColumnIndex,
        });
        worksheet[headerAddress] = {
          t: "s",
          v: FAMILY_HEADER,
        };
        range.e.c = familyColumnIndex;
      }

      for (let rowIndex = headerRowIndex + 1; rowIndex <= range.e.r; rowIndex += 1) {
        const cellAddress = XLSX.utils.encode_cell({
          r: rowIndex,
          c: familyColumnIndex,
        });
        worksheet[cellAddress] = {
          t: "s",
          v: selectedFamily,
        };
      }

      worksheet["!ref"] = XLSX.utils.encode_range(range);

      XLSX.writeFile(workbook, buildOutputFileName(file.name), {
        compression: true,
      });

      setStatus("success");
      setMessage("Archivo generado correctamente. La descarga debería iniciar automáticamente.");
    } catch (error) {
      setStatus("error");
      setMessage(
        error?.message ||
          "No se pudo procesar el Excel. Verifica el formato del archivo."
      );
    } finally {
      setIsProcessing(false);
    }
  };

  return (
    <section className="compat-page">
      <div className="compat-upload-layout upload-family-layout">
        <header className="compat-upload-header">
          <h1>Carga de Familias</h1>
          <p>Asigna una familia a tus archivos de compatibilidades desde Excel.</p>
        </header>

        <div className="compat-upload-card">
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
              <option key={family.code} value={family.code}>
                {family.label}
              </option>
            ))}
          </select>
        </div>

        <div className="file-wrapper">
          <input
            ref={fileInputRef}
            className="file-input"
            type="file"
            accept=".xlsx,.xls,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel"
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
            disabled={!file || !selectedFamily || isProcessing}
          >
            {isProcessing ? "Procesando..." : "Ejecutar Carga de Familias"}
          </button>
        </div>

        {message && <p className={`status-message ${status}`}>{message}</p>}
        </div>
      </div>
    </section>
  );
}

export default UploadFamily;
