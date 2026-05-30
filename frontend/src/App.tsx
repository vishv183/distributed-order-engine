import { useEffect, useState } from 'react';
import './index.css';

interface Order {
  id: number;
  account_id: number;
  sku: string;
  ordered_quantity: number;
  calculated_total: number;
  status: string;
  error_log: any;
}

interface AuditLog {
  id: string;
  order_id: number;
  tool_executed: string;
  arguments_passed: any;
  tool_output: any;
  timestamp: string;
}

const PAGE_SIZE = 100;

function App() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [totalOrders, setTotalOrders] = useState(0);
  const [currentPage, setCurrentPage] = useState(0);
  
  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);

  // Fetch orders with pagination
  useEffect(() => {
    setLoading(true);
    const skip = currentPage * PAGE_SIZE;
    
    fetch(`http://localhost:8000/api/orders/?skip=${skip}&limit=${PAGE_SIZE}`)
      .then(res => res.json())
      .then(data => {
        setOrders(data.data);
        setTotalOrders(data.total);
        setLoading(false);
      })
      .catch(err => {
        console.error("Failed to fetch orders", err);
        setLoading(false);
      });
  }, [currentPage]);

  // Fetch audit logs when an order is selected
  useEffect(() => {
    if (!selectedOrder) return;
    
    fetch(`http://localhost:8000/api/orders/${selectedOrder.id}/audit-logs/`)
      .then(res => res.json())
      .then(data => {
        setAuditLogs(data);
      })
      .catch(err => console.error("Failed to fetch audit logs", err));
  }, [selectedOrder]);

  const getStatusBadgeColors = (status: string) => {
    switch(status.toLowerCase()) {
      case 'pending': return 'bg-amber-100 text-amber-700 border-amber-200';
      case 'ready_for_shipping': return 'bg-green-100 text-green-700 border-green-200';
      case 'exceptional_hold': return 'bg-red-100 text-red-700 border-red-200';
      default: return 'bg-slate-100 text-slate-700 border-slate-200';
    }
  };

  const totalPages = Math.ceil(totalOrders / PAGE_SIZE);

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 font-sans flex flex-col">
      {/* Premium Header */}
      <header className="bg-white border-b border-slate-200 shadow-sm sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-blue-900 tracking-tight flex items-center gap-2">
              <span className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center text-white text-lg shadow-md shadow-blue-200">
                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                  <path d="M10 2a6 6 0 00-6 6v3.586l-.707.707A1 1 0 004 14h12a1 1 0 00.707-1.707L16 11.586V8a6 6 0 00-6-6zm0 16a3 3 0 01-3-3h6a3 3 0 01-3 3z" />
                </svg>
              </span>
              B2B Exception Engine
            </h1>
            <p className="text-sm text-slate-500 mt-1 ml-10">Real-time order triage and agent audit dashboard</p>
          </div>
          <div className="flex items-center gap-4 text-sm font-medium">
             <span className="px-3 py-1 bg-blue-50 text-blue-700 rounded-full border border-blue-100 shadow-inner">
               System Live
             </span>
          </div>
        </div>
      </header>

      {/* Main Content Dashboard */}
      <main className="max-w-7xl mx-auto px-6 py-8 flex-1 w-full">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 h-full">
          
          {/* Left Column: Orders List */}
          <div className="col-span-1 lg:col-span-5 flex flex-col h-[calc(100vh-160px)]">
            <div className="bg-white border border-slate-200 rounded-xl shadow-sm flex flex-col h-full overflow-hidden">
              <div className="px-5 py-4 border-b border-slate-100 bg-slate-50/50 flex justify-between items-center shrink-0">
                <h2 className="font-semibold text-slate-800">Recent Orders</h2>
                <span className="text-xs font-medium bg-blue-100 text-blue-800 px-2.5 py-0.5 rounded-full">
                  Total: {totalOrders}
                </span>
              </div>
              
              <div className="flex-1 overflow-y-auto">
                {loading ? (
                  <div className="flex h-full items-center justify-center text-slate-400 italic">
                    <div className="animate-pulse flex flex-col items-center gap-2">
                       <div className="w-8 h-8 border-4 border-blue-200 border-t-blue-600 rounded-full animate-spin"></div>
                       <span>Loading orders...</span>
                    </div>
                  </div>
                ) : orders.length === 0 ? (
                  <div className="flex h-full items-center justify-center text-slate-500">No orders found.</div>
                ) : (
                  <ul className="divide-y divide-slate-100">
                    {orders.map(order => {
                      const isActive = selectedOrder?.id === order.id;
                      return (
                        <li 
                          key={order.id} 
                          onClick={() => setSelectedOrder(order)}
                          className={`
                            group p-5 cursor-pointer transition-all duration-200
                            ${isActive 
                              ? 'bg-blue-50/80 border-l-4 border-l-blue-600' 
                              : 'bg-white hover:bg-slate-50 border-l-4 border-l-transparent'
                            }
                          `}
                        >
                          <div className="flex justify-between items-start mb-2">
                            <span className={`font-semibold ${isActive ? 'text-blue-900' : 'text-slate-800 group-hover:text-blue-700'}`}>
                              Order #{order.id}
                            </span>
                            <span className={`text-[10px] uppercase font-bold tracking-wider px-2 py-1 rounded-md border ${getStatusBadgeColors(order.status)}`}>
                              {order.status.replace(/_/g, ' ')}
                            </span>
                          </div>
                          <div className="flex gap-4 text-sm text-slate-600 mt-2">
                            <div className="flex flex-col">
                              <span className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold">SKU</span>
                              <span className="font-medium">{order.sku.substring(0, 15)}{order.sku.length > 15 ? '...' : ''}</span>
                            </div>
                            <div className="flex flex-col">
                              <span className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold">Qty</span>
                              <span className="font-medium">{order.ordered_quantity}</span>
                            </div>
                            <div className="flex flex-col ml-auto text-right">
                              <span className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold">Total</span>
                              <span className={`font-semibold ${isActive ? 'text-blue-700' : 'text-slate-700'}`}>
                                ${order.calculated_total.toFixed(2)}
                              </span>
                            </div>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
              
              {/* Pagination Controls */}
              <div className="border-t border-slate-200 bg-white px-5 py-3 flex items-center justify-between shrink-0">
                <div className="flex gap-2">
                  <button 
                    disabled={currentPage === 0 || loading}
                    onClick={() => setCurrentPage(0)}
                    className="px-3 py-1 text-xs font-semibold text-slate-600 bg-slate-100 hover:bg-slate-200 rounded-md disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    First
                  </button>
                  <button 
                    disabled={currentPage === 0 || loading}
                    onClick={() => setCurrentPage(p => Math.max(0, p - 1))}
                    className="px-3 py-1 text-sm font-medium text-slate-600 bg-slate-100 hover:bg-slate-200 rounded-md disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    Previous
                  </button>
                </div>
                <span className="text-xs text-slate-500 font-medium">
                  Page {currentPage + 1} of {totalPages || 1}
                </span>
                <div className="flex gap-2">
                  <button 
                    disabled={currentPage >= totalPages - 1 || loading}
                    onClick={() => setCurrentPage(p => p + 1)}
                    className="px-3 py-1 text-sm font-medium text-slate-600 bg-slate-100 hover:bg-slate-200 rounded-md disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    Next
                  </button>
                  <button 
                    disabled={currentPage >= totalPages - 1 || loading}
                    onClick={() => setCurrentPage(totalPages - 1)}
                    className="px-3 py-1 text-xs font-semibold text-slate-600 bg-slate-100 hover:bg-slate-200 rounded-md disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    Last
                  </button>
                </div>
              </div>

            </div>
          </div>

          {/* Right Column: Order Details & Audit Trail */}
          <div className="col-span-1 lg:col-span-7 h-[calc(100vh-160px)]">
            <div className="bg-white border border-slate-200 rounded-xl shadow-sm h-full flex flex-col overflow-hidden">
              <div className="px-6 py-4 border-b border-slate-100 bg-white flex justify-between items-center shadow-sm z-10 shrink-0">
                <h2 className="font-semibold text-slate-800">Order Triage & Audit Trail</h2>
                {selectedOrder && (
                   <span className="text-sm text-slate-500">Ref: #{selectedOrder.id}</span>
                )}
              </div>
              
              <div className="flex-1 overflow-y-auto p-6 bg-slate-50/30">
                {!selectedOrder ? (
                  <div className="h-full flex flex-col items-center justify-center text-slate-400">
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-16 w-16 mb-4 text-slate-200" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                    </svg>
                    <p>Select an order from the list to view its audit history.</p>
                  </div>
                ) : (
                  <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
                    
                    {/* Exception Error Block */}
                    {selectedOrder.error_log && (
                      <div className="mb-8 bg-red-50 border border-red-100 rounded-xl overflow-hidden shadow-sm">
                        <div className="bg-red-500/10 px-4 py-2 border-b border-red-100 flex items-center gap-2">
                           <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 text-red-600" viewBox="0 0 20 20" fill="currentColor">
                            <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                          </svg>
                          <h4 className="text-sm font-semibold text-red-800 uppercase tracking-wider">Current Exception State</h4>
                        </div>
                        <div className="p-4">
                          <pre className="text-xs text-red-900 font-mono whitespace-pre-wrap break-words">
                            {typeof selectedOrder.error_log === 'object' 
                              ? JSON.stringify(selectedOrder.error_log, null, 2)
                              : selectedOrder.error_log}
                          </pre>
                        </div>
                      </div>
                    )}

                    <div className="flex items-center gap-3 mb-6 mt-4">
                       <h3 className="text-lg font-bold text-slate-800">Agent Execution Log</h3>
                       <div className="h-px bg-slate-200 flex-1"></div>
                    </div>
                    
                    {auditLogs.length === 0 ? (
                      <div className="text-center py-10 bg-white border border-slate-200 border-dashed rounded-xl">
                        <p className="text-slate-500">No AI agent interactions recorded for this order yet.</p>
                        <p className="text-xs text-slate-400 mt-1">Exceptions trigger background processing automatically.</p>
                      </div>
                    ) : (
                      <div className="relative pl-4 space-y-8 before:absolute before:inset-0 before:ml-5 before:-translate-x-px md:before:mx-auto md:before:translate-x-0 before:h-full before:w-0.5 before:bg-gradient-to-b before:from-transparent before:via-slate-200 before:to-transparent">
                        {auditLogs.map((log, index) => (
                          <div key={log.id} className="relative flex items-center justify-between md:justify-normal md:odd:flex-row-reverse group is-active">
                            
                            {/* Timeline Dot */}
                            <div className="flex items-center justify-center w-8 h-8 rounded-full border-4 border-white bg-blue-500 shadow shrink-0 md:order-1 md:group-odd:-translate-x-1/2 md:group-even:translate-x-1/2 z-10">
                               <span className="text-white text-[10px] font-bold">{index + 1}</span>
                            </div>
                            
                            {/* Content Card */}
                            <div className="w-[calc(100%-3rem)] md:w-[calc(50%-2rem)] bg-white p-4 rounded-xl shadow-sm border border-slate-200 hover:shadow-md hover:border-blue-300 transition-all">
                              <div className="flex items-center justify-between gap-2 flex-wrap mb-3">
                                <span className="text-sm font-bold text-blue-700 font-mono bg-blue-50 px-2 py-1 rounded-md border border-blue-100 break-all">
                                  {log.tool_executed}
                                </span>
                                <time className="text-xs font-medium text-slate-400 shrink-0 whitespace-nowrap">
                                  {new Date(log.timestamp).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'})}
                                </time>
                              </div>
                              
                              <div className="space-y-3">
                                <div className="bg-slate-50 rounded-lg p-3 border border-slate-100">
                                  <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Arguments Passed</span>
                                  <pre className="text-[11px] text-slate-700 font-mono whitespace-pre-wrap break-words">
                                    {typeof log.arguments_passed === 'string' 
                                      ? (() => { try { return JSON.stringify(JSON.parse(log.arguments_passed), null, 2) } catch(e) { return log.arguments_passed } })()
                                      : JSON.stringify(log.arguments_passed, null, 2)}
                                  </pre>
                                </div>
                                
                                <div className="bg-blue-50/50 rounded-lg p-3 border border-blue-100">
                                  <span className="block text-[10px] font-bold text-blue-400 uppercase tracking-wider mb-1">Tool Output</span>
                                  <pre className="text-[11px] text-blue-900 font-mono whitespace-pre-wrap break-words">
                                    {typeof log.tool_output === 'string'
                                      ? (() => { try { return JSON.stringify(JSON.parse(log.tool_output), null, 2) } catch(e) { return log.tool_output } })()
                                      : JSON.stringify(log.tool_output, null, 2)}
                                  </pre>
                                </div>
                              </div>
                            </div>

                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>

        </div>
      </main>
    </div>
  );
}

export default App;
