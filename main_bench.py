"""
This file is part of the accompanying code to our manuscript:

Kratzert, F., Klotz, D., Shalev, G., Klambauer, G., Hochreiter, S., Nearing, G., "Benchmarking
a Catchment-Aware Long Short-Term Memory Network (LSTM) for Large-Scale Hydrological Modeling".
submitted to Hydrol. Earth Syst. Sci. Discussions (2019)

You should have received a copy of the Apache-2.0 license along with the code. If not,
see <https://opensource.org/licenses/Apache-2.0>
"""

import argparse
import time
import json
import pickle
import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path, PosixPath
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from tqdm import tqdm

from papercode.datasets import CamelsH5, CamelsTXT
from papercode.datautils import (add_camels_attributes, load_attributes,
                                 rescale_features)
from papercode.ealstm import EALSTM
from papercode.lstm import LSTM
from papercode.metrics import calc_nse
from papercode.nseloss import NSELoss
from papercode.utils import create_h5_files, get_basin_list

###########
# Globals #
###########

# timing constants

NS_CONV = 1000000000.0

# fixed settings for all experiments
GLOBAL_SETTINGS = {
    #    'batch_size': 256,
    'clip_norm': True,
    'clip_value': 1,
    #    'dropout': 0.4,
    #    'epochs': 30,
    #    'hidden_size': 256,
    'initial_forget_gate_bias': 5,
    'log_interval': 50,
    #    'learning_rate': 1e-3,
    'seq_length': 270,
    'train_start': pd.to_datetime('01101999', format='%d%m%Y'),
    'train_end': pd.to_datetime('30092008', format='%d%m%Y'),
    'val_start': pd.to_datetime('01101989', format='%d%m%Y'),
    'val_end': pd.to_datetime('30091999', format='%d%m%Y')
}

basin_list = {}
basin_list["US"] = ["01022500",
                    "02011400",
                    "03010655",
                    "04015330",
                    "05291000",
                    "06221400",
                    "07291000",
                    "08013000",
                    "09035800",
                    "10234500",
                    "11124500",
                    "12010000",
                    "13011500",
                    "14020000",
                    "01669520",
                    "02481510",
                    "03604000",
                    "04256000",
                    "05120500",
                    "06921200",
                    "07346045",
                    "08380500",
                    "09513780",
                    "10259000",
                    "11532500",
                    "12488500"]
basin_list["NE"] = ["01031500",
                    "01055000",
                    "01078000",
                    "01181000",
                    "01195100",
                    "01440000",
                    "04233000",
                    "03049000",
                    "01568000",
                    "01669520",
                    "03213700",
                    "03300400",
                    "07014500",
                    "06906800",
                    "05062500",
                    "04024430",
                    "04074950",
                    "05412500",
                    "05495000",
                    "05525500",
                    "04124000",
                    "04196800",
                    "04185000",
                    "03237500",
                    "03346000",
                    "03366500"
                    ]
basin_list["SE"] = ["02053200",
                    "02108000",
                    "02128000",
                    "03498500",
                    "02196000",
                    "02202600",
                    "02314500",
                    "02235200",
                    "02310947",
                    "02361000",
                    "02469800",
                    "02472000",
                    "07291000",
                    "07375000",
                    "08013000",
                    "07362100",
                    "07056000",
                    "03604000",
                    "03592718",
                    "02464000",
                    "08014500",
                    "02327100",
                    "02221525",
                    "02350900",
                    "07060710",
                    "02140991"
                    ]
basin_list["NW"] = ["05123400",
                    "05057000",
                    "06353000",
                    "06477500",
                    "06601000",
                    "06784000",
                    "06876700",
                    "07149000",
                    "06847900",
                    "06452000",
                    "06289000",
                    "06224000",
                    "06154410",
                    "06043500",
                    "09081600",
                    "10242000",
                    "13023000",
                    "12358500",
                    "13337000",
                    "14020000",
                    "11383500",
                    "10249300",
                    "14301000",
                    "11522500",
                    "10396000",
                    "09312600"
                    ]
basin_list["SW"] = ["11151300",
                    "11124500",
                    "10259200",
                    "09505800",
                    "08070000",
                    "09447800",
                    "09430500",
                    "09484600",
                    "08324000",
                    "07226500",
                    "07301500",
                    "08079600",
                    "07315700",
                    "08086290",
                    "08190500",
                    "08194200",
                    "08101000",
                    "07346045",
                    "08066200",
                    "08164300",
                    "08189500",
                    "07148400",
                    "08175000",
                    "07299670",
                    "09386900",
                    "07315200"
                    ]
# check if GPU is available
DEVICE = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")


###############
# Prepare run #
###############


def get_args() -> Dict:
    """Parse input arguments

    Returns
    -------
    dict
        Dictionary containing the run config.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=["train", "evaluate", "eval_robustness"])
    parser.add_argument('--camels_root', type=str, help="Root directory of CAMELS data set")
    parser.add_argument('--seed', type=int, required=False, help="Random seed")
    parser.add_argument('--exp_dir',
                        type=str,
                        help="For train mode. Experiments runs directory.")
    parser.add_argument('--run_name',
                        type=str,
                        help="For train mode. Directory name of the run.")
    parser.add_argument('--run_dir', type=str, help="For evaluation mode. Path to run directory.")
    parser.add_argument('--cache_data',
                        type=bool,
                        default=False,
                        help="If True, loads all data into memory")
    parser.add_argument('--num_workers',
                        type=int,
                        default=12,
                        help="Number of parallel threads for data loading")
    parser.add_argument('--no_static',
                        type=bool,
                        default=False,
                        help="If True, trains LSTM without static features")
    parser.add_argument('--concat_static',
                        type=bool,
                        default=False,
                        help="If True, train LSTM with static feats concatenated at each time step")
    parser.add_argument('--use_mse',
                        type=bool,
                        default=False,
                        help="If True, uses mean squared error as loss function.")
    parser.add_argument('--use_partial_attribs',
                        type=bool,
                        default=False,
                        help="If True, uses only the top 10 characteristics.")
    parser.add_argument('--epochs',
                        type=int,
                        default=30,
                        help="Number of epochs for training.")
    parser.add_argument('--batch_size',
                        type=int,
                        default=256,
                        help="batch size.")
    parser.add_argument('--hidden_size',
                        type=int,
                        default=256,
                        help="hidden size.")
    parser.add_argument('--dropout',
                        type=float,
                        default=0.4,
                        help="dropout.")
    parser.add_argument('--learning_rate',
                        type=float,
                        default=1e-3,
                        help="initial learning rate.")
    parser.add_argument('--region',
                        type=str,
                        default="US",
                        help="US region to study.")
    cfg = vars(parser.parse_args())

    # Validation checks
    if (cfg["mode"] == "train") and (cfg["seed"] is None):
        # generate random seed for this run
        cfg["seed"] = int(np.random.uniform(low=0, high=1e6))

    if (cfg["mode"] in ["evaluate", "eval_robustness"]) and (cfg["run_dir"] is None):
        raise ValueError("In evaluation mode a run directory (--run_dir) has to be specified")

    # combine global settings with user config
    cfg.update(GLOBAL_SETTINGS)

    if cfg["mode"] == "train":
        # print config to terminal
        for key, val in cfg.items():
            print(f"{key}: {val}")

    # convert path to PosixPath object
    cfg["camels_root"] = Path(cfg["camels_root"])
    if cfg["run_dir"] is not None:
        cfg["run_dir"] = Path(cfg["run_dir"].replace('"', ''))
    if cfg["exp_dir"] is not None:
        cfg["exp_dir"] = Path(cfg["exp_dir"].replace('"', ''))
    return cfg


def _setup_run(cfg: Dict) -> Dict:
    """Create folder structure for this run

    Parameters
    ----------
    cfg : dict
        Dictionary containing the run config

    Returns
    -------
    dict
        Dictionary containing the updated run config
    """
    # now = datetime.now()
    # day = f"{now.day}".zfill(2)
    # month = f"{now.month}".zfill(2)
    # hour = f"{now.hour}".zfill(2)
    # minute = f"{now.minute}".zfill(2)
    # run_name = f'run_{day}{month}_{hour}{minute}_seed{cfg["seed"]}'
    run_name = cfg["run_name"]
    path = Path(__file__).absolute().parent
    if cfg['exp_dir']:
        path = cfg['exp_dir']
    cfg['run_dir'] = path / "runs" / run_name
    if not cfg["run_dir"].is_dir():
        cfg["train_dir"] = cfg["run_dir"] / 'data' / 'train'
        cfg["train_dir"].mkdir(parents=True)
        cfg["val_dir"] = cfg["run_dir"] / 'data' / 'val'
        cfg["val_dir"].mkdir(parents=True)
    else:
        raise RuntimeError(f"There is already a folder at {cfg['run_dir']}")

    # dump a copy of cfg to run directory
    with (cfg["run_dir"] / 'cfg.json').open('w') as fp:
        temp_cfg = {}
        for key, val in cfg.items():
            if isinstance(val, PosixPath):
                temp_cfg[key] = str(val)
            elif isinstance(val, pd.Timestamp):
                temp_cfg[key] = val.strftime(format="%d%m%Y")
            else:
                temp_cfg[key] = val
        json.dump(temp_cfg, fp, sort_keys=True, indent=4)

    return cfg


def _prepare_data(cfg: Dict, basins: List) -> Dict:
    """Preprocess training data.

    Parameters
    ----------
    cfg : dict
        Dictionary containing the run config
    basins : List
        List containing the 8-digit USGS gauge id

    Returns
    -------
    dict
        Dictionary containing the updated run config
    """
    # create database file containing the static basin attributes
    cfg["db_path"] = str(cfg["run_dir"] / "attributes.db")
    add_camels_attributes(cfg["camels_root"], db_path=cfg["db_path"],
                          userPartialAttribs=cfg["use_partial_attribs"])

    # create .h5 files for train and validation data
    cfg["train_file"] = cfg["train_dir"] / 'train_data.h5'
    create_h5_files(camels_root=cfg["camels_root"],
                    out_file=cfg["train_file"],
                    basins=basins,
                    dates=[cfg["train_start"], cfg["train_end"]],
                    with_basin_str=True,
                    seq_length=cfg["seq_length"])

    return cfg


################
# Define Model #
################


class Model(nn.Module):
    """Wrapper class that connects LSTM/EA-LSTM with fully connceted layer"""

    def __init__(self,
                 input_size_dyn: int,
                 input_size_stat: int,
                 hidden_size: int,
                 initial_forget_bias: int = 5,
                 dropout: float = 0.0,
                 concat_static: bool = False,
                 no_static: bool = False):
        """Initialize model.

        Parameters
        ----------
        input_size_dyn: int
            Number of dynamic input features.
        input_size_stat: int
            Number of static input features (used in the EA-LSTM input gate).
        hidden_size: int
            Number of LSTM cells/hidden units.
        initial_forget_bias: int
            Value of the initial forget gate bias. (default: 5)
        dropout: float
            Dropout probability in range(0,1). (default: 0.0)
        concat_static: bool
            If True, uses standard LSTM otherwise uses EA-LSTM
        no_static: bool
            If True, runs standard LSTM
        """
        super(Model, self).__init__()
        self.input_size_dyn = input_size_dyn
        self.input_size_stat = input_size_stat
        self.hidden_size = hidden_size
        self.initial_forget_bias = initial_forget_bias
        self.dropout_rate = dropout
        self.concat_static = concat_static
        self.no_static = no_static

        if self.concat_static or self.no_static:
            self.lstm = LSTM(input_size=input_size_dyn,
                             hidden_size=hidden_size,
                             initial_forget_bias=initial_forget_bias)
        else:
            self.lstm = EALSTM(input_size_dyn=input_size_dyn,
                               input_size_stat=input_size_stat,
                               hidden_size=hidden_size,
                               initial_forget_bias=initial_forget_bias)

        self.dropout = nn.Dropout(p=dropout)
        self.fc = nn.Linear(hidden_size, 1)

    def forward(self, x_d: torch.Tensor, x_s: torch.Tensor = None) \
            -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Run forward pass through the model.

        Parameters
        ----------
        x_d : torch.Tensor
            Tensor containing the dynamic input features of shape [batch, seq_length, n_features]
        x_s : torch.Tensor, optional
            Tensor containing the static catchment characteristics, by default None

        Returns
        -------
        out : torch.Tensor
            Tensor containing the network predictions
        h_n : torch.Tensor
            Tensor containing the hidden states of each time step
        c_n : torch,Tensor
            Tensor containing the cell states of each time step
        """
        if self.concat_static or self.no_static:
            h_n, c_n = self.lstm(x_d)
        else:
            h_n, c_n = self.lstm(x_d, x_s)
        last_h = self.dropout(h_n[:, -1, :])
        out = self.fc(last_h)
        return out, h_n, c_n


###########################
# Train or evaluate model #
###########################


def train(cfg):
    """Train model.

    Parameters
    ----------
    cfg : Dict
        Dictionary containing the run config
    """
    # fix random seeds
    random.seed(cfg["seed"])
    np.random.seed(cfg["seed"])
    torch.cuda.manual_seed(cfg["seed"])
    torch.manual_seed(cfg["seed"])

    # basins = get_basin_list()
    basins = basin_list[cfg["region"]]

    # create folder structure for this run
    cfg = _setup_run(cfg)

    # prepare data for training
    t1_prep_total = time.time_ns()
    cfg = _prepare_data(cfg=cfg, basins=basins)
    t2_prep_total = time.time_ns()

    # prepare PyTorch DataLoader
    t1_pre_data_loader_total = time.time_ns()
    ds = CamelsH5(h5_file=cfg["train_file"],
                  basins=basins,
                  db_path=cfg["db_path"],
                  concat_static=cfg["concat_static"],
                  cache=cfg["cache_data"],
                  no_static=cfg["no_static"])
    loader = DataLoader(ds,
                        batch_size=cfg["batch_size"],
                        shuffle=True,
                        num_workers=cfg["num_workers"])
    t2_pre_data_loader_total = time.time_ns()
    
    print(f"Total Data Preparation Time {(t2_prep_total - t1_prep_total)/NS_CONV} s")
    print(f"Total Data Loader Time {(t2_pre_data_loader_total - t1_pre_data_loader_total)/NS_CONV} s")

    # create model and optimizer
    num_stat_attribs = 27
    num_dyn_attribs = 32
    if cfg["use_partial_attribs"]:
        num_stat_attribs = 10
        num_dyn_attribs = 15
    input_size_stat = 0 if cfg["no_static"] else num_stat_attribs
    input_size_dyn = 5 if (cfg["no_static"] or not cfg["concat_static"]) else num_dyn_attribs
    model = Model(input_size_dyn=input_size_dyn,
                  input_size_stat=input_size_stat,
                  hidden_size=cfg["hidden_size"],
                  initial_forget_bias=cfg["initial_forget_gate_bias"],
                  dropout=cfg["dropout"],
                  concat_static=cfg["concat_static"],
                  no_static=cfg["no_static"]).to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=cfg["learning_rate"])

    # define loss function
    if cfg["use_mse"]:
        loss_func = nn.MSELoss()
    else:
        loss_func = NSELoss()

    # reduce learning rates after each 10 epochs
    learning_rates = {11: 5e-4, 21: 1e-4}

    for epoch in range(1, cfg["epochs"] + 1):
        # set new learning rate
        if epoch in learning_rates.keys():
            for param_group in optimizer.param_groups:
                param_group["lr"] = learning_rates[epoch]

        t1_epoch_time_total = time.time_ns()
        t_epoch_pure_training_time_total = train_epoch(model, optimizer, loss_func, loader, cfg, epoch, cfg["use_mse"])
        t2_epoch_time_total = time.time_ns()
        
        t_epoch_pure_data_time_total = (t2_epoch_time_total - t1_epoch_time_total) - t_epoch_pure_training_time_total

        model_path = cfg["run_dir"] / f"model_epoch{epoch}.pt"
        torch.save(model.state_dict(), str(model_path))
        print(f"Total Data Manipulation Time Per Epoch[{epoch}] : {t_epoch_pure_data_time_total / NS_CONV} s")

        
def train_epoch(model: nn.Module, optimizer: torch.optim.Optimizer, loss_func: nn.Module,
                loader: DataLoader, cfg: Dict, epoch: int, use_mse: bool):
    """Train model for a single epoch.

    Parameters
    ----------
    model : nn.Module
        The PyTorch model to train
    optimizer : torch.optim.Optimizer
        Optimizer used for weight updating
    loss_func : nn.Module
        The loss function, implemented as a PyTorch Module
    loader : DataLoader
        PyTorch DataLoader containing the training data in batches.
    cfg : Dict
        Dictionary containing the run config
    epoch : int
        Current Number of epoch
    use_mse : bool
        If True, loss_func is nn.MSELoss(), else NSELoss() which expects addtional std of discharge
        vector

    """
    model.train()

    # process bar handle
    pbar = tqdm(loader, file=sys.stdout)
    pbar.set_description(f'# Epoch {epoch}')
    t_optimizer_zero_time_total = 0        
    t_forward_time_total = 0
    t_loss_time_total = 0
    t_backward_time_total = 
    t_optimizer_time_total = 0
    t_mist_time_total = 0
    
    # Iterate in batches over training set
    for data in pbar:
        # delete old gradients
        t1 = time.time_ns()
        optimizer.zero_grad()
        t2= time.time_ns()
        t_optimizer_zero_time_total += t2-t1

        # forward pass through LSTM
        if len(data) == 3:
            x, y, q_stds = data
            t1 = time.time_ns()
            x, y, q_stds = x.to(DEVICE), y.to(DEVICE), q_stds.to(DEVICE)
            predictions = model(x)[0]
            t2 = time.time_ns()
            t_forward_time_total += t2 - t1

        # forward pass through EALSTM
        elif len(data) == 4:
            x_d, x_s, y, q_stds = data
            t1 = time.time_ns()
            x_d, x_s, y = x_d.to(DEVICE), x_s.to(DEVICE), y.to(DEVICE)
            predictions = model(x_d, x_s[:, 0, :])[0]
            t2 = time.time_ns()
            t_forward_time_total += t2- t1            

        # MSELoss
        if use_mse:
            t1 = time.time_ns()
            loss = loss_func(predictions, y)
            t2 = time.time_ns()
            t_loss_time_total += t2 - t1

        # NSELoss needs std of each basin for each sample
        else:
            t1 = time.time_ns()
            q_stds = q_stds.to(DEVICE)
            loss = loss_func(predictions, y, q_stds)
            t2 = time.time_ns()
            t_loss_time_total += t2 - t1

        # calculate gradients
        t1 = time.time_ns()
        loss.backward()
        t2 = time.time_ns()
        t_backward_time_total += t2 - t1
        
        t1 = time.time_ns()
        if cfg["clip_norm"]:
            torch.nn.utils.clip_grad_norm_(model.parameters(), cfg["clip_value"])

        # perform parameter update
        optimizer.step()        

        pbar.set_postfix_str(f"Loss: {loss.item():5f}")
        t2 = time.time_ns()
        t_optimizer_time_total += t2 - t1
    return t_optimizer_zero_time_total + t_forward_time_total + t_loss_time_total + t_backward_time_total + t_optimizer_time_total + t_mist_time_total

def evaluate(user_cfg: Dict):
    """Train model for a single epoch.

    Parameters
    ----------
    user_cfg : Dict
        Dictionary containing the user entered evaluation config

    """
    with open(user_cfg["run_dir"] / 'cfg.json', 'r') as fp:
        run_cfg = json.load(fp)

    # basins = get_basin_list()
    basins = basin_list[run_cfg["region"]]

    # get attribute means/stds
    db_path = str(user_cfg["run_dir"] / "attributes.db")
    attributes = load_attributes(db_path=db_path,
                                 basins=basins,
                                 drop_lat_lon=True)
    means = attributes.mean()
    stds = attributes.std()

    # create model and optimizer
    num_stat_attribs = 27
    num_dyn_attribs = 32
    if run_cfg["use_partial_attribs"]:
        num_stat_attribs = 10
        num_dyn_attribs = 15
    input_size_stat = 0 if run_cfg["no_static"] else num_stat_attribs
    input_size_dyn = 5 if (run_cfg["no_static"] or not run_cfg[
        "concat_static"]) else num_dyn_attribs
    model = Model(input_size_dyn=input_size_dyn,
                  input_size_stat=input_size_stat,
                  hidden_size=run_cfg["hidden_size"],
                  dropout=run_cfg["dropout"],
                  concat_static=run_cfg["concat_static"],
                  no_static=run_cfg["no_static"]).to(DEVICE)

    # load trained model
    weight_file = user_cfg["run_dir"] / f"model_epoch{run_cfg['epochs']}.pt"
    model.load_state_dict(torch.load(weight_file, map_location=DEVICE))

    date_range = pd.date_range(start=GLOBAL_SETTINGS["val_start"], end=GLOBAL_SETTINGS["val_end"])
    results = {}
    for basin in tqdm(basins):
        ds_test = CamelsTXT(camels_root=user_cfg["camels_root"],
                            basin=basin,
                            dates=[GLOBAL_SETTINGS["val_start"], GLOBAL_SETTINGS["val_end"]],
                            is_train=False,
                            seq_length=run_cfg["seq_length"],
                            with_attributes=True,
                            attribute_means=means,
                            attribute_stds=stds,
                            concat_static=run_cfg["concat_static"],
                            db_path=db_path)
        loader = DataLoader(ds_test, batch_size=1024, shuffle=False, num_workers=4)

        preds, obs = evaluate_basin(model, loader)

        df = pd.DataFrame(data={'qobs': obs.flatten(), 'qsim': preds.flatten()}, index=date_range)

        results[basin] = df

    _store_results(user_cfg, run_cfg, results)


def evaluate_basin(model: nn.Module, loader: DataLoader) -> Tuple[np.ndarray, np.ndarray]:
    """Evaluate model on a single basin

    Parameters
    ----------
    model : nn.Module
        The PyTorch model to train
    loader : DataLoader
        PyTorch DataLoader containing the basin data in batches.

    Returns
    -------
    preds : np.ndarray
        Array containing the (rescaled) network prediction for the entire data period
    obs : np.ndarray
        Array containing the observed discharge for the entire data period

    """
    model.eval()

    preds, obs = None, None

    with torch.no_grad():
        for data in loader:
            if len(data) == 2:
                x, y = data
                x, y = x.to(DEVICE), y.to(DEVICE)
                p = model(x)[0]
            elif len(data) == 3:
                x_d, x_s, y = data
                x_d, x_s, y = x_d.to(DEVICE), x_s.to(DEVICE), y.to(DEVICE)
                p = model(x_d, x_s[:, 0, :])[0]

            if preds is None:
                preds = p.detach().cpu()
                obs = y.detach().cpu()
            else:
                preds = torch.cat((preds, p.detach().cpu()), 0)
                obs = torch.cat((obs, y.detach().cpu()), 0)

        preds = rescale_features(preds.numpy(), variable='output')
        obs = obs.numpy()
        # set discharges < 0 to zero
        preds[preds < 0] = 0

    return preds, obs


def eval_robustness(user_cfg: Dict):
    """Evaluate model robustness of EA-LSTM

    In this experiment, gaussian noise with increasing scale is added to the static features to
    evaluate the model robustness against pertubations of the static catchment characteristics.
    For each scale, 50 noise vectors are drawn.

    Parameters
    ----------
    user_cfg : Dict
        Dictionary containing the user entered evaluation config

    Raises
    ------
    NotImplementedError
        If the run_dir specified points not to a EA-LSTM model folder.
    """
    random.seed(user_cfg["seed"])
    np.random.seed(user_cfg["seed"])

    # fixed settings for this analysis
    n_repetitions = 50
    scales = [0.1 * i for i in range(11)]

    with open(user_cfg["run_dir"] / 'cfg.json', 'r') as fp:
        run_cfg = json.load(fp)

    if run_cfg["concat_static"] or run_cfg["no_static"]:
        raise NotImplementedError("This function is only implemented for EA-LSTM models")

    # basins = get_basin_list()
    basins = basin_list[run_cfg["region"]]

    # get attribute means/stds
    db_path = str(user_cfg["run_dir"] / "attributes.db")
    attributes = load_attributes(db_path=db_path,
                                 basins=basins,
                                 drop_lat_lon=True)
    means = attributes.mean()
    stds = attributes.std()

    # initialize Model
    model = Model(input_size_dyn=5,
                  input_size_stat=27,
                  hidden_size=run_cfg["hidden_size"],
                  dropout=run_cfg["dropout"]).to(DEVICE)
    weight_file = user_cfg["run_dir"] / f"model_epoch{run_cfg['epochs']}.pt"
    model.load_state_dict(torch.load(weight_file, map_location=DEVICE))

    overall_results = {}
    # process bar handle
    pbar = tqdm(basins, file=sys.stdout)
    for basin in pbar:
        ds_test = CamelsTXT(camels_root=user_cfg["camels_root"],
                            basin=basin,
                            dates=[GLOBAL_SETTINGS["val_start"], GLOBAL_SETTINGS["val_end"]],
                            is_train=False,
                            with_attributes=True,
                            attribute_means=means,
                            attribute_stds=stds,
                            db_path=db_path)
        loader = DataLoader(ds_test, batch_size=len(ds_test), shuffle=False, num_workers=0)
        basin_results = defaultdict(list)
        step = 1
        for scale in scales:
            for _ in range(1 if scale == 0.0 else n_repetitions):
                noise = np.random.normal(loc=0, scale=scale, size=27).astype(np.float32)
                noise = torch.from_numpy(noise).to(DEVICE)
                nse = eval_with_added_noise(model, loader, noise)
                basin_results[scale].append(nse)
                pbar.set_postfix_str(f"Basin progress: {step}/{(len(scales) - 1) * n_repetitions + 1}")
                step += 1

        overall_results[basin] = basin_results
    out_file = (Path(__file__).absolute().parent /
                f'results/{user_cfg["run_dir"].name}_model_robustness.p')
    if not out_file.parent.is_dir():
        out_file.parent.mkdir(parents=True)
    with out_file.open("wb") as fp:
        pickle.dump(overall_results, fp)


def eval_with_added_noise(model: torch.nn.Module, loader: DataLoader, noise: torch.Tensor) -> float:
    """Evaluate model on a single basin with added noise

    Parameters
    ----------
    model : nn.Module
        The PyTorch model to train
    loader : DataLoader
        PyTorch DataLoader containing the basin data in batches.
    noise : torch.Tensor
        Tensor containing the noise for this evaluation run.

    Returns
    -------
    float
        Nash-Sutcliff-Efficiency of the simulations with added noise.
    """
    model.eval()
    preds, obs = None, None
    with torch.no_grad():
        for x_d, x_s, y in loader:
            x_d, x_s, y = x_d.to(DEVICE), x_s.to(DEVICE), y.to(DEVICE)
            batch_noise = noise.repeat(*x_s.size()[:2], 1)
            x_s = x_s.add(batch_noise)
            y_hat = model(x_d, x_s[:, 0, :])[0]

            if preds is None:
                preds = y_hat.detach().cpu()
                obs = y.detach().cpu()
            else:
                preds = torch.cat((preds, y_hat.detach().cpu()), 0)
                obs = torch.cat((obs, y.detach().cpu()), 0)

        obs = obs.numpy()
        preds = rescale_features(preds.numpy(), variable='output')

        # set discharges < 0 to zero
        preds[preds < 0] = 0

        nse = calc_nse(obs[obs >= 0], preds[obs >= 0])
        return nse


def _store_results(user_cfg: Dict, run_cfg: Dict, results: pd.DataFrame):
    """Store results in a pickle file.

    Parameters
    ----------
    user_cfg : Dict
        Dictionary containing the user entered evaluation config
    run_cfg : Dict
        Dictionary containing the run config loaded from the cfg.json file
    results : pd.DataFrame
        DataFrame containing the observed and predicted discharge.

    """
    if run_cfg["no_static"]:
        file_name = user_cfg["run_dir"] / f"lstm_no_static_seed{run_cfg['seed']}.p"
    else:
        if run_cfg["concat_static"]:
            file_name = user_cfg["run_dir"] / f"lstm_seed{run_cfg['seed']}.p"
        else:
            file_name = user_cfg["run_dir"] / f"ealstm_seed{run_cfg['seed']}.p"

    with (file_name).open('wb') as fp:
        pickle.dump(results, fp)

    print(f"Sucessfully store results at {file_name}")


if __name__ == "__main__":
    config = get_args()
    globals()[config["mode"]](config)