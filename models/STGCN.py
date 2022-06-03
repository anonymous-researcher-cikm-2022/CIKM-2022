
import sys
import math
import torch
import torch.nn as nn
import torch.nn.init as init
import torch.nn.functional as F
import numpy as np
import pandas as pd
from scipy.sparse.linalg import eigs
from torchsummary import summary
import sys
import  time
sys.path.append("..")
from utils.Utils import *


class align(nn.Module):
    def __init__(self, c_in, c_out):
        super(align, self).__init__()
        self.c_in = c_in
        self.c_out = c_out
        if c_in > c_out:
            self.conv1x1 = nn.Conv2d(c_in, c_out, 1)

    def forward(self, x):
        if self.c_in > self.c_out:
            return self.conv1x1(x)
        if self.c_in < self.c_out:
            return F.pad(x, [0, 0, 0, 0, 0, self.c_out - self.c_in, 0, 0])
        return x

class temporal_conv_layer(nn.Module):
    def __init__(self, kt, c_in, c_out, act="relu"):
        super(temporal_conv_layer, self).__init__()
        self.kt = kt
        self.act = act
        self.c_out = c_out
        self.align = align(c_in, c_out)
        if self.act == "GLU":
            self.conv = nn.Conv2d(c_in, c_out * 2, (kt, 1), 1)
        else:
            self.conv = nn.Conv2d(c_in, c_out, (kt, 1), 1)

    def forward(self, x):
        x_in = self.align(x)[:, :, self.kt - 1:, :]
        if self.act == "GLU":
            x_conv = self.conv(x)
            return (x_conv[:, :self.c_out, :, :] + x_in) * torch.sigmoid(x_conv[:, self.c_out:, :, :])
        if self.act == "sigmoid":
            return torch.sigmoid(self.conv(x) + x_in)
        return torch.relu(self.conv(x) + x_in)


class spatio_conv_layer(nn.Module):
    def __init__(self, ks, c, Lk):
        super(spatio_conv_layer, self).__init__()
        self.Lk = Lk
        self.theta = nn.Parameter(torch.FloatTensor(c, c, ks))
        self.b = nn.Parameter(torch.FloatTensor(1, c, 1, 1))
        self.reset_parameters()

    def reset_parameters(self):
        init.kaiming_uniform_(self.theta, a=math.sqrt(5))
        fan_in, _ = init._calculate_fan_in_and_fan_out(self.theta)
        bound = 1 / math.sqrt(fan_in)
        init.uniform_(self.b, -bound, bound)

    def forward(self, x):
        x_c = torch.einsum("knm,bitm->bitkn", self.Lk, x)
        x_gc = torch.einsum("iok,bitkn->botn", self.theta, x_c) + self.b
        return torch.relu(x_gc + x)


def cosine_distance_torch(x1, x2=None, eps=1e-8):
    x2 = x1 if x2 is None else x2
    w1 = x1.norm(p=2, dim=-1, keepdim=True)
    w2 = w1 if x2 is x1 else x2.norm(p=2, dim=-1, keepdim=True)
    return 1 - torch.matmul(x1, x2.permute(0,2,1)) / (w1 * w2).clamp(min=eps)

class st_conv_block(nn.Module):
    def __init__(self, ks, kt, n, c, p, Lk):
        super(st_conv_block, self).__init__()
        self.tconv1 = temporal_conv_layer(kt, c[0], c[1], "GLU")
        self.sconv = spatio_conv_layer(ks, c[1], Lk)
        self.tconv2 = temporal_conv_layer(kt, c[1], c[2])
        self.ln = nn.LayerNorm([n, c[2]])

        self.dropout = nn.Dropout(p)

    def forward(self, x):
        x_t1 = self.tconv1(x)
        x_s = self.sconv(x_t1)
        x_t2 = self.tconv2(x_s)
        x_ln = self.ln(x_t2.permute(0, 2, 3, 1)).permute(0, 3, 1, 2)        
        return self.dropout(x_ln)

class output_layer(nn.Module):
    def __init__(self, c, T, n):
        super(output_layer, self).__init__()
        self.tconv1 = temporal_conv_layer(T, c, c, "GLU")
        self.ln = nn.LayerNorm([n, c])
        self.tconv2 = temporal_conv_layer(1, c, c, "sigmoid")
        self.fc = nn.Conv2d(c, 1, 1)

    def forward(self, x):
        x_t1 = self.tconv1(x)
        x_ln = self.ln(x_t1.permute(0, 2, 3, 1)).permute(0, 3, 1, 2)
        x_t2 = self.tconv2(x_ln)
        return self.fc(x_t2)

def temporal_scoring(A,k):
    values, indices = torch.topk(A, k, dim=-1, largest=False)
    # values = values.max(dim=2)[0].unsqueeze(-1).repeat(1, 1, A.shape[2])
    # A = A.scatter_(2, indices, values)
    local_rechability_density = 1/ A.permute(0, 2, 1).gather(2, indices).mean(-1)
    temporal_diff = local_rechability_density.unsqueeze(1).repeat(1, A.shape[1], 1).gather(2, indices).mean(
        -1) / local_rechability_density
    return temporal_diff

class STGCN(nn.Module):
    def __init__(self, ks, kt, bs, T, n, Lk, p, W):
        super(STGCN, self).__init__()
        self.W=W
        self.st_conv1 = st_conv_block(ks, kt, n, bs[0], p, Lk)
        self.st_conv2 = st_conv_block(ks, kt, n, bs[1], p, Lk)
        self.output = output_layer(bs[1][2], T - 4 * (kt - 1), n)

    def forward(self, x, pp=None,k=None,if_cl=None):
        x_st1 = self.st_conv1(x)
        x_st2 = self.st_conv2(x_st1)
        if self.training and if_cl:
            x_st2_vec=x_st2.reshape(-1,x_st2.shape[1]*x_st2.shape[2],x_st2.shape[-1]).permute(0,2,1)
            cosine_dist = cosine_distance_torch(x_st2_vec)
            rec_cosine = temporal_scoring(cosine_dist,k=k)
            spatial_diff=0
            for i  in range(len(self.W)):
                spatial_diff+=(cosine_dist*self.W[i:i+1]).sum(dim=2)/self.W[i:i+1].sum(dim=2)
            nomalized_temporal_diff = (rec_cosine - rec_cosine.mean()) / torch.sqrt(rec_cosine.var())
            nomalized_spatial_diff = (spatial_diff - spatial_diff.mean()) / torch.sqrt(spatial_diff.var())
            nomalized_diff = nomalized_temporal_diff+nomalized_spatial_diff
            score = nomalized_diff.argsort(dim=1, descending=False).argsort(dim=1, descending=False)
            score[score < score.shape[1] * pp] = -1
            score[score > -1] = 0
            score = score / pp
            self.score = score.view(x_st2.shape[0], 1, 1, x_st2.shape[3]) * -1  # .repeat(1, data.shape[1], 1, 1)
            x_st2 = x_st2 * score

            y_pred = self.output(x_st2)
        else:
            y_pred = self.output(x_st2)

        return y_pred
    
def weight_matrix_torch(W, sigma2=0.1, epsilon=0.5):
    '''
    :param sigma2: float, scalar of matrix W.
    :param epsilon: float, thresholds to control the sparsity of matrix W.
    :param scaling: bool, whether applies numerical scaling on W.
    :return: np.ndarray, [n_route, n_route].
    '''
    n = W.shape[0]
    W = W /10000
    W[W==0]=torch.inf
    W2 = W * W
    W_mask = (torch.ones([n, n]) - torch.eye(n))
    return torch.exp(-W2 / sigma2) * (torch.exp(-W2 / sigma2) >= epsilon) * W_mask + torch.eye(n)

def weight_matrix(W, sigma2=0.1, epsilon=0.5):
    '''
    :param sigma2: float, scalar of matrix W.
    :param epsilon: float, thresholds to control the sparsity of matrix W.
    :param scaling: bool, whether applies numerical scaling on W.
    :return: np.ndarray, [n_route, n_route].
    '''
    n = W.shape[0]
    W = W /10000
    W[W==0]=np.inf
    W2 = W * W
    W_mask = (np.ones([n, n]) - np.identity(n))
    return np.exp(-W2 / sigma2) * (np.exp(-W2 / sigma2) >= epsilon) * W_mask

def scaled_laplacian(A):
    n = A.shape[0]
    d = np.sum(A, axis=1)
    L = np.diag(d) - A
    for i in range(n):
        for j in range(n):
            if d[i] > 0 and d[j] > 0:
                L[i, j] /= np.sqrt(d[i] * d[j])
    lam = np.linalg.eigvals(L).max().real
    return 2 * L / lam - np.eye(n)

def cheb_poly(L, Ks):
    n = L.shape[0]
    LL = [np.eye(n), L[:]]
    for i in range(2, Ks):
        LL.append(np.matmul(2 * L, LL[-1]) - LL[-2])
    return np.asarray(LL)
