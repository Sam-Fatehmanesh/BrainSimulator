import torch
from torch import nn
import torch.nn.functional as F
import numpy as np
from BrainSimulator.models.cnn import CNNLayer, DeCNNLayer
from BrainSimulator.models.mlp import MLP
from BrainSimulator.custom_functions.utils import STMNsampler

class VariationalAutoEncoder(nn.Module):
    def __init__(self, image_height, image_width, n_distributions=16, n_categories=32):
        super(VariationalAutoEncoder, self).__init__()

        self.image_height = int(image_height)
        self.image_width = int(image_width)
        self.n_distributions = n_distributions  # Number of categorical distributions
        self.n_categories = n_categories  # Number of categories per distribution
        self.latent_size = n_distributions * n_categories  # Total size of latent space
        
        self.scalings = [8, 4, 2]  # Down/Upsampling factors

        # Calculate padded dimensions to make them divisible by total scaling
        total_scaling = int(np.prod(self.scalings))
        self.padded_height = int(np.ceil(image_height / total_scaling) * total_scaling)
        self.padded_width = int(np.ceil(image_width / total_scaling) * total_scaling)
        
        # Calculate padding
        self.pad_height = int(self.padded_height - image_height)
        self.pad_width = int(self.padded_width - image_width)
        self.pad_top = int(self.pad_height // 2)
        self.pad_bottom = int(self.pad_height - self.pad_top)
        self.pad_left = int(self.pad_width // 2)
        self.pad_right = int(self.pad_width - self.pad_left)

        self.latent_channels_size = self.n_distributions

        # Calculate sizes after CNN layers
        self.post_cnn_height = int(self.padded_height // total_scaling)
        self.post_cnn_width = int(self.padded_width // total_scaling)
        self.post_cnn_encoder_size = int(self.post_cnn_height * self.post_cnn_width * self.latent_channels_size)
        
        self.encoder = nn.Sequential(
            CNNLayer(1, 64, 3),
            nn.MaxPool2d(self.scalings[0], stride=self.scalings[0]),

            CNNLayer(64, 256, 3),
            nn.MaxPool2d(self.scalings[1], stride=self.scalings[1]),

            CNNLayer(256, self.latent_channels_size, 3),
            nn.MaxPool2d(self.scalings[2], stride=self.scalings[2]),

            nn.Flatten(),
            MLP(3, self.post_cnn_encoder_size, self.latent_size, self.n_distributions * self.n_categories)
        )

        self.softmax_act = nn.Softmax(dim=1)
        self.sampler = STMNsampler()

        # Decoder
        self.decoder = nn.Sequential(
            MLP(3, self.latent_size, self.latent_size, self.post_cnn_encoder_size),
            
            nn.Unflatten(1, (self.latent_channels_size, self.post_cnn_height, self.post_cnn_width)),

            DeCNNLayer(self.latent_channels_size, 256, kernel_size=self.scalings[2], stride=self.scalings[2], padding=0),

            DeCNNLayer(256, 64, kernel_size=self.scalings[1], stride=self.scalings[1], padding=0),

            DeCNNLayer(64, 1, kernel_size=self.scalings[0], stride=self.scalings[0], padding=0),
            
            nn.Sigmoid(),
        )

    def pad_input(self, x):
        return F.pad(x, (self.pad_left, self.pad_right, self.pad_top, self.pad_bottom), mode='constant', value=0)

    def unpad_output(self, x):
        if self.pad_top + self.pad_bottom > 0:
            x = x[:, :, self.pad_top:-self.pad_bottom] if self.pad_bottom > 0 else x[:, :, self.pad_top:]
        if self.pad_left + self.pad_right > 0:
            x = x[:, :, :, self.pad_left:-self.pad_right] if self.pad_right > 0 else x[:, :, :, self.pad_left:]
        return x

    def encode(self, x):
        batch_dim = x.shape[0]
        # Pad input
        x = self.pad_input(x)
        x = self.encoder(x)
        # Reshape to (batch * n_distributions, n_categories)
        x = x.view(batch_dim * self.n_distributions, self.n_categories)
        distributions = self.softmax_act(x)
        # Add small epsilon to prevent zero probabilities
        distributions = 0.99 * distributions + 0.01 * torch.ones_like(distributions) / self.n_categories
        sample = self.sampler(distributions)
        # Reshape samples back to (batch, n_distributions * n_categories)
        sample = sample.view(batch_dim, self.n_distributions * self.n_categories)
        # Reshape distributions back to (batch, n_distributions * n_categories)
        distributions = distributions.view(batch_dim, self.n_distributions * self.n_categories)
        return sample, distributions

    def decode(self, z):
        x = self.decoder(z)
        # Remove padding
        x = self.unpad_output(x)
        return x
    
    def forward(self, x):
        latent_sample, latent_distribution = self.encode(x)
        decoded_out = self.decode(latent_sample)
        return decoded_out, latent_sample, latent_distribution