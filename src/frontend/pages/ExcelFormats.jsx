import { Download, FileSpreadsheet } from "lucide-react";
import "../styles/ExcelFormats.css";

const EXCEL_FORMATS = [
  {
    id: "fotografias",
    process: "Actualización de fotografías",
    description:
      "Actualiza las imágenes de cada publicación con su MLC y los enlaces de las fotografías.",
    fileName: "Actualización Fotografias.xlsx",
    assetName: "actualizacion-fotografias.xlsx",
  },
  {
    id: "precio-estado-stock",
    process: "Actualización de precio, estado y stock",
    description:
      "Modifica el precio, estado y stock de tus publicaciones de Mercado Libre.",
    fileName: "Actualizacion Precio-estado-stock.xlsx",
    assetName: "actualizacion-precio-estado-stock.xlsx",
  },
  {
    id: "descripciones",
    process: "Actualización de descripciones",
    description:
      "Actualiza el texto de la descripción de cada publicación, identificándola por su MLC.",
    fileName: "Actualizar Descripciones.xlsx",
    assetName: "actualizar-descripciones.xlsx",
  },
  {
    id: "compatibilidades",
    process: "Informar compatibilidades",
    description:
      "Agrega los vehículos compatibles con cada publicación, indicando sus características y la familia del repuesto.",
    fileName: "Informar Compatibilidades.xlsx",
    assetName: "informar-compatibilidades.xlsx",
  },
  {
    id: "no-compatibilidades",
    process: "Informar no compatibilidades",
    description:
      "Usa este formato para informar las publicaciones sin compatibilidades, identificándolas por su MLC.",
    fileName: "Informar No Compatibilidades.xlsx",
    assetName: "informar-no-compatibilidades.xlsx",
  },
];

export default function ExcelFormats() {
  return (
    <section className="excel-formats-page">
      <div className="excel-formats-layout">
        <header className="excel-formats-header">
          <h1>Formatos Excel</h1>
          <p>
            Elige el proceso que necesitas y descarga su formato Excel. Reemplaza
            las filas de ejemplo por tus datos y conserva los nombres de las
            columnas.
          </p>
        </header>

        <div className="excel-formats-grid">
          {EXCEL_FORMATS.map((format) => (
            <article
              key={format.id}
              className="excel-formats-card"
              aria-labelledby={`excel-format-${format.id}`}
            >
              <div className="excel-formats-card-heading">
                <span className="excel-formats-card-icon" aria-hidden="true">
                  <FileSpreadsheet size={24} strokeWidth={1.75} />
                </span>
                <h2 id={`excel-format-${format.id}`}>{format.process}</h2>
              </div>

              <p className="excel-formats-card-description">
                {format.description}
              </p>

              <div className="excel-formats-file">
                <span className="excel-formats-file-label">Archivo del formato</span>
                <span className="excel-formats-file-name">{format.fileName}</span>
              </div>

              <a
                className="excel-formats-download"
                href={`${import.meta.env.BASE_URL}excel-formats/${format.assetName}`}
                download={format.fileName}
                aria-label={`Descargar formato de ${format.process.toLowerCase()}`}
              >
                <Download size={18} aria-hidden="true" />
                Descargar formato Excel
              </a>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
