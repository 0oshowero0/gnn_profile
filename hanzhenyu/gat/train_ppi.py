"""
Graph Attention Networks (PPI Dataset) in DGL using SPMV optimization.
Multiple heads are also batched together for faster training.
Compared with the original paper, this code implements
early stopping.
References
----------
Paper: https://arxiv.org/abs/1710.10903
Author's code: https://github.com/PetarV-/GAT
Pytorch implementation: https://github.com/Diego999/pyGAT
"""

import numpy as np
import torch
import dgl
import torch.nn.functional as F
import argparse
from sklearn.metrics import f1_score
from gat import GAT
from dgl.data.ppi import PPIDataset
from dgl.dataloading import GraphDataLoader

import time
from datetime import datetime
import networkx as nx
from torch.profiler import profile, record_function, ProfilerActivity


def evaluate(feats, model, subgraph, labels, loss_fcn):
    with torch.no_grad():
        model.eval()
        model.g = subgraph
        for layer in model.gat_layers:
            layer.g = subgraph
        output = model(feats.float())
        loss_data = loss_fcn(output, labels.float())
        predict = np.where(output.data.cpu().numpy() >= 0., 1, 0)
        score = f1_score(labels.data.cpu().numpy(),
                         predict, average='micro')
        return score, loss_data.item()
        
def main(args):
    if args.gpu<0:
        device = torch.device("cpu")
    else:
        device = torch.device("cuda:" + str(args.gpu))

    batch_size = args.batch_size
    cur_step = 0
    patience = args.patience
    best_score = -1
    best_loss = 10000
    # define loss function
    loss_fcn = torch.nn.BCEWithLogitsLoss()
    # create the dataset
    begin_load_data_time = datetime.now()
    train_dataset = PPIDataset(mode='train')
    valid_dataset = PPIDataset(mode='valid')
    test_dataset = PPIDataset(mode='test')
    end_load_data_time = datetime.now()
    
    print('Load Data in: '+str((end_load_data_time-begin_load_data_time).total_seconds()))
    
    train_dataloader = GraphDataLoader(train_dataset, batch_size=batch_size)
    valid_dataloader = GraphDataLoader(valid_dataset, batch_size=batch_size)
    test_dataloader = GraphDataLoader(test_dataset, batch_size=batch_size)
    g = train_dataset[0]

    '''
    #################################################################################################################
    # 评估数据集情况
    num_nodes = []
    num_edges = []
    connected_graph_num = []
    max_connected_node_num = []
    max_in_degree = []
    min_in_degree = []
    mean_in_degree = []
    std_dev_in_degree = []
    for graph in train_dataset:
        num_nodes.append(graph.num_nodes())
        num_edges.append(graph.num_edges())
        degrees = graph.in_degrees().float()
        mean_in_degree.append(float(degrees.mean()))
        max_in_degree.append(float(degrees.max()))
        min_in_degree.append(float(degrees.min()))
        std_dev_in_degree.append(float(degrees.std()))

        g = graph.to_networkx()

        i = 0
        sub_g_node_num = []
        for subg in nx.connected_components(g.to_undirected()):
            i += 1
            sub_g_node_num.append(len(subg))
        connected_graph_num.append(i)
        max_connected_node_num.append(max(sub_g_node_num))

    for graph in valid_dataset:
        num_nodes.append(graph.num_nodes())
        num_edges.append(graph.num_edges())
        degrees = graph.in_degrees().float()
        mean_in_degree.append(float(degrees.mean()))
        max_in_degree.append(float(degrees.max()))
        min_in_degree.append(float(degrees.min()))
        std_dev_in_degree.append(float(degrees.std()))

        g = graph.to_networkx()

        i = 0
        sub_g_node_num = []
        for subg in nx.connected_components(g.to_undirected()):
            i += 1
            sub_g_node_num.append(len(subg))
        connected_graph_num.append(i)
        max_connected_node_num.append(max(sub_g_node_num))


    for graph in test_dataset:
        num_nodes.append(graph.num_nodes())
        num_edges.append(graph.num_edges())
        degrees = graph.in_degrees().float()
        mean_in_degree.append(float(degrees.mean()))
        max_in_degree.append(float(degrees.max()))
        min_in_degree.append(float(degrees.min()))
        std_dev_in_degree.append(float(degrees.std()))

        g = graph.to_networkx()

        i = 0
        sub_g_node_num = []
        for subg in nx.connected_components(g.to_undirected()):
            i += 1
            sub_g_node_num.append(len(subg))
        connected_graph_num.append(i)
        max_connected_node_num.append(max(sub_g_node_num))
    
    print(num_nodes)
    print(num_edges)
    print(connected_graph_num)
    print(max_connected_node_num)
    print(max_in_degree)
    print(min_in_degree)
    print(mean_in_degree)
    print(std_dev_in_degree)

    #################################################################################################################
    '''


    n_classes = train_dataset.num_labels
    num_feats = g.ndata['feat'].shape[1]
    g = g.int().to(device)
    heads = ([args.num_heads] * args.num_layers) + [args.num_out_heads]
    # define the model
    model = GAT(g,
                args.num_layers,
                num_feats,
                args.num_hidden,
                n_classes,
                heads,
                F.elu,
                args.in_drop,
                args.attn_drop,
                args.alpha,
                args.residual)
    # define the optimizer
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    model = model.to(device)
    
    total_forward_time = 0
    total_backward_time = 0
    
    for epoch in range(args.epochs):
        model.train()
        loss_list = []
        for batch, subgraph in enumerate(train_dataloader):
            subgraph = subgraph.to(device)
            model.g = subgraph
            for layer in model.gat_layers:
                layer.g = subgraph
            
            begin_forward_time = time.time()
            with record_function('forward'):
                logits = model(subgraph.ndata['feat'].float())
            end_forward_time = time.time()
            total_forward_time += (end_forward_time - begin_forward_time)
        
            loss = loss_fcn(logits, subgraph.ndata['label'])
            
            optimizer.zero_grad()
            begin_backward_time = time.time()
            with record_function('backward'):
                loss.backward()
                optimizer.step()
            end_backward_time = time.time()
            total_backward_time += (end_backward_time - begin_backward_time)
            
            loss_list.append(loss.item())
        loss_data = np.array(loss_list).mean()
        print("Epoch {:05d} | Loss: {:.4f}".format(epoch + 1, loss_data))
        if epoch % 5 == 0:
            score_list = []
            val_loss_list = []
            for batch, subgraph in enumerate(valid_dataloader):
                subgraph = subgraph.to(device)
                score, val_loss = evaluate(subgraph.ndata['feat'], model, subgraph, subgraph.ndata['label'], loss_fcn)
                score_list.append(score)
                val_loss_list.append(val_loss)
            mean_score = np.array(score_list).mean()
            mean_val_loss = np.array(val_loss_list).mean()
            print("Val F1-Score: {:.4f} ".format(mean_score))
            # early stop
            if mean_score > best_score or best_loss > mean_val_loss:
                if mean_score > best_score and best_loss > mean_val_loss:
                    val_early_loss = mean_val_loss
                    val_early_score = mean_score
                best_score = np.max((mean_score, best_score))
                best_loss = np.min((best_loss, mean_val_loss))
                cur_step = 0
            else:
                cur_step += 1
                if cur_step == patience:
                    break
    test_score_list = []
    for batch, subgraph in enumerate(test_dataloader):
        subgraph = subgraph.to(device)
        score, test_loss = evaluate(subgraph.ndata['feat'], model, subgraph, subgraph.ndata['label'], loss_fcn)
        test_score_list.append(score)
    print("Test F1-Score: {:.4f}".format(np.array(test_score_list).mean()))
    print('Avg Forward Time: ' + str(total_forward_time/args.epochs))
    print('Avg Backward Time: ' + str(total_backward_time/args.epochs))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='GAT')
    parser.add_argument("--gpu", type=int, default=-1,
                        help="which GPU to use. Set -1 to use CPU.")
    parser.add_argument("--epochs", type=int, default=400,
                        help="number of training epochs")
    parser.add_argument("--num-heads", type=int, default=4,
                        help="number of hidden attention heads")
    parser.add_argument("--num-out-heads", type=int, default=6,
                        help="number of output attention heads")
    parser.add_argument("--num-layers", type=int, default=2,
                        help="number of hidden layers")
    parser.add_argument("--num-hidden", type=int, default=256,
                        help="number of hidden units")
    parser.add_argument("--residual", action="store_true", default=True,
                        help="use residual connection")
    parser.add_argument("--in-drop", type=float, default=0,
                        help="input feature dropout")
    parser.add_argument("--attn-drop", type=float, default=0,
                        help="attention dropout")
    parser.add_argument("--lr", type=float, default=0.005,
                        help="learning rate")
    parser.add_argument('--weight-decay', type=float, default=0,
                        help="weight decay")
    parser.add_argument('--alpha', type=float, default=0.2,
                        help="the negative slop of leaky relu")
    parser.add_argument('--batch-size', type=int, default=2,
                        help="batch size used for training, validation and test")
    parser.add_argument('--patience', type=int, default=10,
                        help="used for early stop")
    args = parser.parse_args()
    print(args)

    with profile(activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA],with_stack=True, record_shapes=True, use_cuda=True) as p:
        main(args)
    p.export_chrome_trace('profile_gat-PPI.json')
    print(p.key_averages().table(sort_by="cuda_time_total"))
    #print(p.key_averages().table(sort_by="cuda_time_total", row_limit=10))

    # old version，not right
    # print("Total CPU Time (microseconds):")
    # print(sum([item.cpu_time for item in p.function_events]))
    # print("Total CUDA Time (microseconds):")
    # print(sum([item.cuda_time for item in p.function_events]))