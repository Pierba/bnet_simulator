import csv, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

SRC = "../comparazioni ultime"
OUT = "thesis/figures_cap4"
SCHED = ["static", "dynamic_adab", "dynamic_acab"]
SCHED_TITLE = {"static": "SBP", "dynamic_adab": "ADAB", "dynamic_acab": "ACAB"}
MODES = ["none", "append", "forward"]
MODE_LABEL = {"none": "Single-hop", "append": "Forwarding aggregato", "forward": "Forwarding singolo"}
MODE_COLOR = {"none": "#1f77b4", "append": "#ff7f0e", "forward": "#2ca02c"}
DENS = [20, 40, 60, 80, 100]
plt.rcParams.update({"font.size":13,"axes.titlesize":15,"axes.labelsize":13,"legend.fontsize":11,
  "figure.dpi":150,"savefig.bbox":"tight","axes.grid":True,"grid.alpha":0.35,"grid.linestyle":"--"})

def chan_dir(c): return os.path.join(SRC, "senza errore" if c=="ideal" else "con errore")
def res_dir(c,m,iv="1.0"):
    tag=f"interval-{iv}_ideal_random" if c=="ideal" else f"interval-{iv}_random"
    return os.path.join(chan_dir(c), f"results_{tag}_{m}")
def read_summary(p):
    val,std={},{}
    with open(p) as f:
        for row in csv.reader(f):
            if len(row)>=2 and row[0]:
                try: val[row[0]]=float(row[1])
                except ValueError: val[row[0]]=row[1]
                try: std[row[0]]=float(row[2])
                except (ValueError,IndexError): std[row[0]]=0.0
    return val,std
def series(c,m,s,metric):
    ys,es=[],[]
    for d in DENS:
        v,sd=read_summary(os.path.join(res_dir(c,m),f"{s}_random_density{d}.csv"))
        ys.append(v[metric]); es.append(sd.get(metric,0.0))
    return np.array(ys),np.array(es)
def avg_neighbors(c):
    out=[]
    for d in DENS:
        vals=[]
        for m in MODES:
            for s in SCHED:
                v,_=read_summary(os.path.join(res_dir(c,m),f"{s}_random_density{d}.csv"))
                vals.append(v["Average Neighbors"])
        out.append(sum(vals)/len(vals))
    return np.array(out)
def save(fig,c,fn):
    sub="senza_errore" if c=="ideal" else "con_errore"
    os.makedirs(os.path.join(OUT,sub),exist_ok=True)
    for ext in ("pdf","png"): fig.savefig(os.path.join(OUT,sub,f"{fn}.{ext}"))
    plt.close(fig); print("wrote",sub,fn)
def bar_figure(c,metric,ylabel,fn,ylim=None,legend_loc="lower left"):
    fig,axes=plt.subplots(1,3,figsize=(13.5,4.4),sharey=True)
    width=0.26; x=np.arange(len(DENS)); neigh=avg_neighbors(c)
    ymax=0; handles=labels=None
    for j,(ax,s) in enumerate(zip(axes,SCHED)):
        for k,m in enumerate(MODES):
            ys,es=series(c,m,s,metric); ymax=max(ymax,(ys+es).max())
            ax.bar(x+(k-1)*width,ys,width,yerr=es,capsize=2.5,
                   error_kw={"lw":0.9,"alpha":0.8},label=MODE_LABEL[m],color=MODE_COLOR[m])
        ax.set_title(SCHED_TITLE[s]); ax.set_xticks(x)
        ax.set_xticklabels([f"{d}\n({n:.1f})" for d,n in zip(DENS,neigh)])
        ax.set_xlabel("Numero di boe\n(tra parentesi: vicini medi)")
        ax.set_axisbelow(True)
        if j==0:
            handles,labels=ax.get_legend_handles_labels()
    axes[0].set_ylabel(ylabel)
    if ylim is None: ylim=(0,min(1.0,ymax*1.12))
    axes[0].set_ylim(*ylim)
    axes[0].legend(handles,labels,loc=legend_loc,framealpha=0.9)
    fig.tight_layout(); save(fig,c,fn)
def growth_figure(c,fn):
    fig,axes=plt.subplots(1,3,figsize=(13.5,4.2),sharey=True)
    for ax,s in zip(axes,SCHED):
        for m in MODES:
            p=os.path.join(res_dir(c,m),f"{s}_random_density100_discovery.csv")
            t,y,sd=[],[],[]
            with open(p) as f:
                rd=csv.reader(f); next(rd)
                for row in rd:
                    tt=float(row[0])
                    if tt>198.5: continue
                    t.append(tt); y.append(float(row[1])); sd.append(float(row[2]))
            t,y,sd=map(np.array,(t,y,sd))
            ax.plot(t,y,color=MODE_COLOR[m],label=MODE_LABEL[m],lw=1.6)
            ax.fill_between(t,np.clip(y-sd,0,100),np.clip(y+sd,0,100),color=MODE_COLOR[m],alpha=0.18,lw=0)
        ax.set_title(SCHED_TITLE[s]); ax.set_xlabel("Tempo (s)"); ax.set_xlim(0,200); ax.set_axisbelow(True)
    axes[0].set_ylabel("% media di rete scoperta"); axes[0].set_ylim(0,100)
    axes[0].legend(loc="lower right",framealpha=0.9)
    fig.tight_layout(); save(fig,c,fn)
for c in ("ideal","error"):
    bar_figure(c,"Avg % Network Discovered","% media di rete scoperta",
               "mode_comparison_avg_percentage_network_discovered_interval-1_0",ylim=(0,100))
    bar_figure(c,"PDR","B-PDR","mode_comparison_pdr_interval-1_0",ylim=(0,1.0))
    bar_figure(c,"Collision Rate","Collision rate","mode_comparison_collision_rate_interval-1_0",legend_loc="upper left")
    growth_figure(c,"mode_comparison_discovery_growth_interval-1_0")
bar_figure("error","Loss Rate","Packet loss","mode_comparison_loss_rate_interval-1_0")
print("done")
