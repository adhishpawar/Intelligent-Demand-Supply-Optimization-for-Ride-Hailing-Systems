export const STATUS_LABELS = {
  REQUESTED: "Requested",
  MATCHING: "Finding your driver",
  DRIVER_ASSIGNED: "Driver assigned",
  DRIVER_ARRIVING: "Driver on the way",
  DRIVER_ARRIVED: "Driver has arrived",
  IN_PROGRESS: "Trip in progress",
  COMPLETED: "Trip completed",
  PAID_PENDING: "Payment processing",
  PAID: "Paid",
  RATED: "Rated — thank you!",
  NO_DRIVER_FOUND: "No drivers available",
  EXPIRED: "Request expired",
  CANCELLED_BY_RIDER: "Cancelled by rider",
  CANCELLED_BY_DRIVER: "Cancelled by driver",
  CANCELLED_BY_SYSTEM: "Cancelled",
};

export function statusPillClass(status) {
  if (status === "MATCHING" || status === "REQUESTED") return "matching";
  if (["DRIVER_ASSIGNED", "DRIVER_ARRIVING", "DRIVER_ARRIVED", "IN_PROGRESS"].includes(status)) return "active";
  if (["PAID", "RATED", "COMPLETED"].includes(status)) return "done";
  if (["NO_DRIVER_FOUND", "EXPIRED", "CANCELLED_BY_RIDER", "CANCELLED_BY_DRIVER", "CANCELLED_BY_SYSTEM"].includes(status)) return "failed";
  return "active";
}

export default function StatusPill({ status }) {
  return (
    <span className={`status-pill ${statusPillClass(status)}`}>
      <span className="dot" />
      {STATUS_LABELS[status] || status}
    </span>
  );
}
